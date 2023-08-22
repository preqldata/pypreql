from typing import List


from preql.core.models import FilterItem, QueryDatasource, SourceType, Concept
from preql.core.processing.nodes.base_node import StrategyNode
from preql.core.processing.nodes.merge_node import MergeNode


class FilterNode(StrategyNode):
    source_type = SourceType.FILTER

    def __init__(
        self,
        mandatory_concepts: List[Concept],
        optional_concepts: List[Concept],
        environment,
        g,
        whole_grain: bool = False,
        parents: List["StrategyNode"] | None = None,
    ):
        super().__init__(
            mandatory_concepts,
            optional_concepts,
            environment,
            g,
            whole_grain=whole_grain,
            parents=parents,
        )

    def _resolve(self) -> QueryDatasource:
        """We need to ensure that any filtered values are removed from the output to avoid inappropriate references"""
        base = super()._resolve()
        filtered_concepts: List[Concept] = [
            c for c in self.mandatory_concepts if isinstance(c.lineage, FilterItem)
        ]
        # to_remove = [c.lineage.content.address for c in filtered_concepts]
        to_remove = []
        base.output_concepts = [
            c for c in base.output_concepts if c.address not in to_remove
        ]
        base.source_map = {
            key: value for key, value in base.source_map.items() if key not in to_remove
        }
        return base
