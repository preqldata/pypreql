from collections import defaultdict
from itertools import combinations
from typing import List, Optional

from preql.core.enums import Purpose
from preql.core.models import (
    Concept,
    Environment,
    BaseJoin,
    Datasource,
)
from typing import Set
from preql.core.processing.nodes import (
    StrategyNode,
    SelectNode,
    MergeNode,
    NodeJoin,
)
from preql.core.exceptions import NoDatasourceException
import networkx as nx
from preql.core.graph_models import concept_to_node, datasource_to_node
from preql.core.processing.utility import PathInfo, path_to_joins
from preql.constants import logger
from preql.core.processing.node_generators.static_select_node import (
    gen_static_select_node,
)
from preql.utility import unique


LOGGER_PREFIX = "[GEN_SELECT_NODE]"

def padding(x:int):
    return '\t'*x

def gen_select_node_from_table(
    all_concepts: List[Concept],
    g: nx.DiGraph,
    environment: Environment,
    depth: int,
    accept_partial: bool = False,
) -> Optional[SelectNode]:
    # if we have only constants
    # we don't need a table
    # so verify nothing, select node will render
    if all([c.purpose == Purpose.CONSTANT for c in all_concepts]):
        return SelectNode(
            output_concepts=all_concepts,
            input_concepts=[],
            environment=environment,
            g=g,
            parents=[],
            depth=depth,
            # no partial for constants
            partial_concepts=[],
        )
    # otherwise, we need to look for a table
    for datasource in environment.datasources.values():
        all_found = True
        for raw_concept in all_concepts:
            # look for connection to abstract grain
            req_concept = raw_concept.with_default_grain()
            # if we don't have a concept in the graph
            # exit early
            if concept_to_node(req_concept) not in g.nodes:
                raise ValueError(concept_to_node(req_concept))
            try:
                path = nx.shortest_path(
                    g,
                    source=datasource_to_node(datasource),
                    target=concept_to_node(req_concept),
                )
            except nx.NodeNotFound as e:
                # just to provide better error
                candidates = [
                    datasource_to_node(datasource),
                    concept_to_node(req_concept),
                ]
                for candidate in candidates:
                    try:
                        g.nodes[candidate]
                    except KeyError:
                        raise SyntaxError(
                            "Could not find node for {}".format(candidate)
                        )
                raise e
            except nx.exception.NetworkXNoPath:
                all_found = False
                break
            # 2023-10-18 - more strict condition then below
            # 2023-10-20 - we need this to get through abstract concepts
            # but we may want to add a filter to avoid using this for anything with lineage
            # if len(path) != 2:
            #     all_found = False
            #     break
            if len([p for p in path if g.nodes[p]["type"] == "datasource"]) != 1:
                all_found = False
                break
            for node in path:
                if g.nodes[node]["type"] == "datasource":
                   
                    continue
                if g.nodes[node]["concept"].address == raw_concept.address:
                    continue
                all_found = False
                break

        if all_found:
            partial_concepts = [
                c.concept
                for c in datasource.columns
                if not c.is_complete
                and c.concept.address in [x.address for x in all_concepts]
            ]
            if not accept_partial and partial_concepts:
                continue
            partial_addresses = [x.concept.address for x in datasource.columns if not x.is_complete]
            return SelectNode(
                input_concepts=[c.concept for c in datasource.columns],
                output_concepts=all_concepts,
                environment=environment,
                g=g,
                parents=[],
                depth=depth,
                partial_concepts=[
                    c for c in all_concepts if c.address in partial_addresses
                ],
                accept_partial=accept_partial
            )
    return None




def gen_select_node(
    concept: Concept,
    local_optional: List[Concept],
    environment: Environment,
    g,
    depth: int,
    accept_partial: bool = False,
    fail_if_not_found: bool = True,
    accept_partial_optional:bool = True,
) -> MergeNode | SelectNode | None:
    all_concepts = [concept] + local_optional
    materialized_addresses = {
        x.address
        for x in all_concepts
        if x.address in [z.address for z in environment.materialized_concepts]
    }
    all_addresses = set([x.address for x in all_concepts])

    if materialized_addresses != all_addresses:
        logger.info(
            f"{padding(depth)}{LOGGER_PREFIX} Skipping select node for {concept.address} as it includes non-materialized concepts {materialized_addresses.difference(all_addresses)} {materialized_addresses} {all_addresses}"
        )
        if fail_if_not_found:
            raise NoDatasourceException(f"No datasource exists for {concept}")
        return None
    ds = None

    # attempt to select all concepts from table
    ds = gen_select_node_from_table(
        [concept] + local_optional,
        g=g,
        environment=environment,
        depth=depth,
        accept_partial=accept_partial,
    )
    if ds:
        logger.info(f'{padding(depth)}{LOGGER_PREFIX} Found select node with all required things')
        return ds
    # if we cannot find a match
    parents:List[StrategyNode] = []
    found:List[Concept] = []
    logger.info(f"{padding(depth)}{LOGGER_PREFIX} looking for multiple sources that can satisfy")
    all_found = False
    for x in reversed(range(1, len(local_optional) + 1)):
        if all_found:
            break
        for combo in combinations(local_optional, x):
            if all_found:
                break
            # filter to just the original ones we need to get
            local_combo = [x for x in combo if x not in found]
            # include core concept as join 
            all_concepts = [concept, *local_combo]
            logger.info(f"{padding(depth)}{LOGGER_PREFIX} starting a loop with {[x.address for x in all_concepts]}")
            
            ds = gen_select_node_from_table(
                all_concepts,
                g=g,
                environment=environment,
                depth=depth+1,
                accept_partial=accept_partial,
            )
            if ds:
                logger.info(f"{padding(depth)}{LOGGER_PREFIX} found a source with {[x.address for x in all_concepts]}")
                parents.append(ds)
                found+=[x for x in ds.output_concepts if x != concept ]
                if {x.address for x in found} == {c.address for c in local_optional}:
                    logger.info(f"{padding(depth)}{LOGGER_PREFIX} found all optional {[c.address for c in local_optional]}")
                    all_found = True
    if parents and (all_found or accept_partial_optional):
        all_partial = [
        c
        for c in all_concepts
        if all(
            [c.address in [x.address for x in p.partial_concepts] for p in parents]
        )
    ]
        return MergeNode(
            output_concepts = [concept] + found,
            input_concepts =  [concept] + found ,
            environment=environment,
            g=g, 
            parents=parents,
            depth=depth,
            partial_concepts=all_partial
        )
    if not accept_partial_optional:
        return None
    ds = gen_select_node_from_table(
        [concept],
        g=g,
        environment=environment,
        depth=depth,
        accept_partial=accept_partial,
    )
    if not ds and fail_if_not_found:
        raise NoDatasourceException(f"No datasource exists for {concept}")
    return ds
