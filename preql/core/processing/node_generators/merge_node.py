from typing import List, Optional

from preql.core.models import (
    Concept,
    Environment,
    Datasource
)
from preql.core.processing.nodes import (
    MergeNode,
    History
)
import networkx as nx
from preql.core.graph_models import concept_to_node, datasource_to_node
from preql.core.processing.utility import PathInfo
from preql.constants import logger
from preql.utility import unique
from preql.core.exceptions import AmbiguousRelationshipResolutionException
from preql.core.processing.utility import padding

LOGGER_PREFIX = "[GEN_MERGE_NODE]"


def reduce_path_concepts(shortest:PathInfo, g) -> set[str]:
    concept_nodes: List[Concept] = []
    # along our path, find all the concepts required
    for key, value in shortest.paths.items():
        concept_nodes += [g.nodes[v]["concept"] for v in value if v.startswith("c~")]
    final: List[Concept] = unique(concept_nodes, "address")
    return set([x.address for x in final])

def identify_ds_join_paths( all_concepts: List[Concept], g, datasource:Datasource, accept_partial: bool = False, fail:bool = False)->PathInfo | None:
    all_found = True
    any_direct_found = False
    paths = {}
    for bitem in all_concepts:
        item = bitem.with_default_grain()
        target_node = concept_to_node(item)
        try:
            path = nx.shortest_path(
                g,
                source=datasource_to_node(datasource),
                target=target_node,
            )
            paths[target_node] = path
            if sum([1 for x in path if x.startswith("ds~")]) == 1:
                any_direct_found = True
        except nx.exception.NodeNotFound:
            # TODO: support Verbose logging mode configuration and reenable these
            all_found = False
            if fail:
                raise
            return None
        except nx.exception.NetworkXNoPath:
            all_found = False
            if fail:
                raise
            return None
    if all_found and any_direct_found:
        partial = [
            c.concept
            for c in datasource.columns
            if not c.is_complete
            and c.concept.address in [x.address for x in all_concepts]
        ]
        if partial and not accept_partial:
            return None
        # join_candidates.append({"paths": paths, "datasource": datasource})
        return PathInfo(paths=paths, datasource=datasource) #{"paths": paths, "datasource": datasource}

def gen_merge_node(
    all_concepts: List[Concept],
    g: nx.DiGraph,
    environment: Environment,
    depth: int,
    source_concepts,
    accept_partial: bool = False,
    history: History | None = None,
) -> Optional[MergeNode]:
    join_candidates: List[PathInfo] = []
    # anchor on datasources
    for datasource in environment.datasources.values():
        path = identify_ds_join_paths(all_concepts, g, datasource, accept_partial)
        if path:
            join_candidates.append(path)
    join_candidates.sort(key=lambda x: sum([len(v) for v in x.paths.values()]))
    if not join_candidates:
        return None
    for join_candidate in join_candidates:
        logger.info(
            f"{padding(depth)}{LOGGER_PREFIX} Join candidate: {join_candidate.paths}"
        )
    join_additions: List[set[str]] = []
    for candidate in join_candidates:
        unique = reduce_path_concepts(candidate, g)
        if unique not in join_additions:
            join_additions.append(unique)
    if not all(
        [x.issubset(y) or y.issubset(x) for x in join_additions for y in join_additions]
    ):
        raise AmbiguousRelationshipResolutionException(
            f"Ambiguous concept join resolution - possible paths =  {join_additions}. Include an additional concept to disambiguate",
            join_additions,
        )
    shortest = sorted(list(join_additions), key=lambda x: len(x))
    final = [environment.concepts[x] for x in shortest[0]]
    if set([x.address for x in final]) == set([x.address for x in all_concepts]):
        # no point in recursing
        # if we could not find an answer
        logger.info(
            f"{padding(depth)}{LOGGER_PREFIX} No additional join candidates could be found"
        )
        return None
    original = {c.address for c in all_concepts}
    new = {c.address for c in final}.difference({c.address for c in all_concepts})
    logger.info(f"{padding(depth)}{LOGGER_PREFIX} sourcing with new concepts {new} added to {original}")
    return source_concepts(
        mandatory_list=final,
        environment=environment,
        g=g,
        depth=depth + 1,
        history=history
    )
