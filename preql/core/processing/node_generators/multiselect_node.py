from preql.core.models import (
    Concept,
    Environment,
    MultiSelect,
)
from preql.core.processing.nodes import MergeNode, NodeJoin
from preql.core.processing.nodes.base_node import concept_list_to_grain, StrategyNode
from typing import List

from preql.core.enums import JoinType
from preql.constants import logger
from preql.core.processing.utility import padding
from preql.core.processing.node_generators.common import concept_to_relevant_joins
from collections import defaultdict
from itertools import combinations

LOGGER_PREFIX = "[GEN_ROWSET_NODE]"


def resolve_join_order(joins: List[NodeJoin]) -> List[NodeJoin]:
    available_aliases: set[str] = set()
    final_joins_pre = [*joins]
    final_joins = []
    while final_joins_pre:
        new_final_joins_pre: List[NodeJoin] = []
        for join in final_joins_pre:
            if not available_aliases:
                final_joins.append(join)
                available_aliases.add(join.left_node)
                available_aliases.add(join.right_node)
            elif join.left_node in available_aliases:
                # we don't need to join twice
                # so whatever join we found first, works
                if join.right_node in available_aliases:
                    continue
                final_joins.append(join)
                available_aliases.add(join.left_node)
                available_aliases.add(join.right_node)
            else:
                new_final_joins_pre.append(join)
        if len(new_final_joins_pre) == len(final_joins_pre):
            remaining = [join.left_node for join in new_final_joins_pre]
            remaining_right = [join.right_node for join in new_final_joins_pre]
            raise SyntaxError(
                f"did not find any new joins, available {available_aliases} remaining is {remaining + remaining_right} "
            )
        final_joins_pre = new_final_joins_pre
    return final_joins


def extra_align_joins(base: MultiSelect, parents: List[StrategyNode]) -> List[NodeJoin]:
    node_merge_concept_map = defaultdict(list)
    output = []
    for align in base.align.items:
        jc = align.gen_concept(base)

        for node in parents:
            for item in align.concepts:
                if item in node.output_concepts:
                    node_merge_concept_map[node].append(jc)

    for left, right in combinations(node_merge_concept_map.keys(), 2):
        matched_concepts = [
            x
            for x in node_merge_concept_map[left]
            if x in node_merge_concept_map[right]
        ]
        output.append(
            NodeJoin(
                left_node=left,
                right_node=right,
                concepts=matched_concepts,
                join_type=JoinType.FULL,
            )
        )

    return resolve_join_order(output)


def gen_multiselect_node(
    concept: Concept,
    local_optional: List[Concept],
    environment: Environment,
    g,
    depth: int,
    source_concepts,
) -> MergeNode | None:
    lineage: MultiSelect = concept.lineage

    base_parents: List[MergeNode] = []
    for select in lineage.selects:
        snode: MergeNode = source_concepts(
            mandatory_list=select.output_components,
            environment=environment,
            g=g,
            depth=depth + 1,
        )
        if not snode:
            logger.info(
                f"{padding(depth)}{LOGGER_PREFIX} Cannot generate multiselect node for {concept}"
            )
            return None
        if select.where_clause:
            snode.conditions = select.where_clause.conditional
        for x in snode.output_concepts:
            merge = lineage.get_merge_concept(x)
            if merge:
                snode.output_concepts.append(merge)
            # clear cache so QPS
            snode.resolution_cache = None
        base_parents.append(snode)
    node = MergeNode(
        input_concepts=[x for y in base_parents for x in y.output_concepts],
        output_concepts=[x for y in base_parents for x in y.output_concepts],
        environment=environment,
        g=g,
        depth=depth,
        parents=base_parents,
        node_joins=extra_align_joins(lineage, base_parents),
    )

    enrichment = set([x.address for x in local_optional])

    rowset_relevant = [
        x
        for x in lineage.derived_concepts
        if x.address == concept.address or x.address in enrichment
    ]
    additional_relevant = [
        x for x in select.output_components if x.address in enrichment
    ]
    # add in other other concepts
    for item in rowset_relevant:
        node.output_concepts.append(item)
    for item in additional_relevant:
        node.output_concepts.append(item)
    if select.where_clause:
        for item in additional_relevant:
            node.partial_concepts.append(item)

    # we need a better API for refreshing a nodes QDS
    node.resolution_cache = node._resolve()

    # assume grain to be outoput of select
    # but don't include anything aggregate at this point
    node.resolution_cache.grain = concept_list_to_grain(
        node.output_concepts, parent_sources=node.resolution_cache.datasources
    )
    possible_joins = concept_to_relevant_joins(additional_relevant)
    if not local_optional:
        logger.info(
            f"{padding(depth)}{LOGGER_PREFIX} no enriched required for rowset node; exiting early"
        )
        return node
    if not possible_joins:
        logger.info(
            f"{padding(depth)}{LOGGER_PREFIX} no possible joins for rowset node; exiting early"
        )
        return node
    if all(
        [x.address in [y.address for y in node.output_concepts] for x in local_optional]
    ):
        logger.info(
            f"{padding(depth)}{LOGGER_PREFIX} all enriched concepts returned from base rowset node; exiting early"
        )
        return node
    enrich_node: MergeNode = source_concepts(  # this fetches the parent + join keys
        # to then connect to the rest of the query
        mandatory_list=additional_relevant + local_optional,
        environment=environment,
        g=g,
        depth=depth + 1,
    )
    if not enrich_node:
        logger.info(
            f"{padding(depth)}{LOGGER_PREFIX} Cannot generate rowset enrichment node for {concept} with optional {local_optional}, returning just rowset node"
        )
        return node

    return MergeNode(
        input_concepts=enrich_node.output_concepts + node.output_concepts,
        output_concepts=node.output_concepts + local_optional,
        environment=environment,
        g=g,
        depth=depth,
        parents=[
            # this node gets the window
            node,
            # this node gets enrichment
            enrich_node,
        ],
        node_joins=[
            NodeJoin(
                left_node=enrich_node,
                right_node=node,
                concepts=concept_to_relevant_joins(additional_relevant),
                filter_to_mutual=False,
                join_type=JoinType.LEFT_OUTER,
            )
        ],
        partial_concepts=node.partial_concepts,
    )
