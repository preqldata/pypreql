import os
from dataclasses import dataclass, field
from typing import List, Optional, Union, Dict, Set

from preql.core.enums import (
    DataType,
    Purpose,
    JoinType,
    Ordering,
    Modifier,
    FunctionType,
    BooleanOperator,
    ComparisonOperator,
)
from preql.utility import unique
from pydantic import BaseModel, validator, Field


class Metadata(BaseModel):
    pass


class Concept(BaseModel):
    name: str
    datatype: DataType
    purpose: Purpose
    grain: "Grain" = Field(default=None)
    metadata: Optional[Metadata] = None
    lineage: Optional["Function"] = None
    namespace: str = ""

    @validator("metadata")
    def metadata_validation(cls, v):
        v = v or Metadata()
        return v

    @validator("namespace", pre=True, always=True)
    def namespace_enforcement(cls, v):
        if not v:
            return "default"
        return v

    @validator("grain", pre=True, always=True)
    def parse_grain(cls, v, values):
        # this is silly - rethink how we do grains
        if not v and values["purpose"] == Purpose.KEY:
            v = Grain(
                components=[
                    Concept(
                        namespace=values.get("namespace", "default"),
                        name=values["name"],
                        datatype=values["datatype"],
                        purpose=values["purpose"],
                        grain=Grain(),
                    )
                ]
            )
        elif not v:
            v = Grain(components=[])
        elif isinstance(v, Concept):
            v = Grain(components=[v])
        return v

    def __eq__(self, other: object):
        if not isinstance(other, Concept):
            return False
        return (
            self.name == other.name
            and self.datatype == other.datatype
            and self.purpose == other.purpose
            and self.namespace == other.namespace
            and self.grain == other.grain
        )

    def __str__(self):
        grain = ",".join([str(c.address) for c in self.grain.components])
        return f"{self.namespace}.{self.name}<{grain}>"

    @property
    def address(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def output(self) -> "Concept":
        return self

    @property
    def safe_address(self) -> str:
        return f"{self.namespace}_{self.name}"

    def with_grain(self, grain: Optional["Grain"] = None) -> "Concept":
        return self.__class__(
            name=self.name,
            datatype=self.datatype,
            purpose=self.purpose,
            metadata=self.metadata,
            lineage=self.lineage,
            grain=grain,
            namespace=self.namespace,
        )

    def with_default_grain(self) -> "Concept":
        if self.purpose == Purpose.KEY:
            grain = Grain(components=[self], nested=True)
        elif self.purpose == Purpose.PROPERTY:
            components = []
            if self.lineage:
                for item in self.lineage.arguments:
                    components += item.sources
            grain = Grain(components=components)
        else:
            grain = self.grain  # type: ignore
        return self.__class__(
            name=self.name,
            datatype=self.datatype,
            purpose=self.purpose,
            metadata=self.metadata,
            lineage=self.lineage,
            grain=grain,
            namespace=self.namespace,
        )

    @property
    def sources(self) -> List["Concept"]:
        if self.lineage:
            output = []
            output += self.lineage.arguments
            # recursively get further lineage
            for item in self.lineage.arguments:
                output += item.sources
            return output
        return []

    @property
    def input(self):
        return [self] + self.sources


@dataclass(eq=True)
class ColumnAssignment:
    alias: str
    concept: Concept
    modifiers: Optional[List[Modifier]] = None

    def is_complete(self):
        return Modifier.PARTIAL not in self.modifiers


@dataclass(eq=True, frozen=True)
class Statement:
    pass


@dataclass(eq=True, frozen=True)
class Function:
    operator: FunctionType
    arguments: List[Concept]
    output_datatype: DataType
    output_purpose: Purpose
    valid_inputs: Optional[Set[DataType]] = None


@dataclass(eq=True)
class ConceptTransform:
    function: Function
    output: Concept

    @property
    def input(self) -> List[Concept]:
        return self.function.arguments


@dataclass(eq=True)
class SelectItem:
    content: Union[Concept, ConceptTransform]

    @property
    def output(self) -> Concept:
        if isinstance(self.content, ConceptTransform):
            return self.content.output
        return self.content

    @property
    def input(self) -> List[Concept]:
        return self.content.input


@dataclass(eq=True)
class OrderItem:
    expr: "Expr"
    order: Ordering


@dataclass(eq=True, frozen=True)
class OrderBy:
    items: List[OrderItem]


@dataclass(eq=True)
class Select:
    selection: Union[List[SelectItem], List[Union[Concept, ConceptTransform]]]
    where_clause: Optional["WhereClause"] = None
    order_by: Optional[OrderBy] = None
    limit: Optional[int] = None

    def __post_init__(self):
        final = []
        for item in self.selection:
            if isinstance(item, (Concept, ConceptTransform)):
                final.append(SelectItem(item))
            else:
                final.append(item)
        self.selection = final

    @property
    def input_components(self) -> List[Concept]:
        output = set()
        output_list = []
        for item in self.selection:
            for concept in item.input:
                if concept.name in output:
                    continue
                output.add(concept.name)
                output_list.append(concept)
        if self.where_clause:
            for concept in self.where_clause.input:
                if concept.name in output:
                    continue
                output.add(concept.name)
                output_list.append(concept)

        return output_list

    @property
    def output_components(self) -> List[Concept]:
        output = []
        for item in self.selection:
            output.append(item.output)
        return output

    @property
    def all_components(self) -> List[Concept]:
        return self.input_components + self.output_components + self.grain.components

    @property
    def grain(self) -> "Grain":
        output = []
        for item in self.output_components:
            if item.purpose == Purpose.KEY:
                output.append(item)
            elif item.purpose == Purpose.PROPERTY and item.grain:
                output += item.grain.components
        return Grain(components=unique(output, "address"))


@dataclass(eq=True, frozen=True)
class Address:
    location: str


class Grain(BaseModel):
    components: List[Concept] = Field(default_factory=list)
    nested: bool = False

    def __init__(self, **kwargs):
        if not kwargs.get("nested", False):
            kwargs["components"] = [
                c.with_default_grain() for c in kwargs.get("components", [])
            ]
        super().__init__(**kwargs)

    def __str__(self):
        if self.abstract:
            return "Grain<Abstract>"
        return "Grain<" + ",".join([c.address for c in self.components]) + ">"

    @property
    def abstract(self):
        return not self.components

    @property
    def set(self):
        return set([c.address for c in self.components])

    def __eq__(self, other: object):
        if not isinstance(other, Grain):
            return False
        return self.set == other.set

    def issubset(self, other: "Grain"):
        return self.set.issubset(other.set)

    def isdisjoint(self, other: "Grain"):
        return self.set.isdisjoint(other.set)

    def intersection(self, other: "Grain") -> "Grain":
        intersection = self.set.intersection(other.set)
        components = [i for i in self.components if i.name in intersection]
        return Grain(components=components)

    def __add__(self, other: "Grain"):
        components = []
        for clist in [self.components, other.components]:
            for component in clist:
                if component in components:
                    continue
                components.append(component)
        return Grain(components=components)

    def __radd__(self, other):
        if other == 0:
            return self
        else:
            return self.__add__(other)


@dataclass
class Datasource:
    identifier: str
    columns: List[ColumnAssignment]
    address: Union[Address, str]
    grain: Grain = field(default_factory=lambda: Grain(components=[]))
    namespace: Optional[str] = ""

    def __hash__(self):
        return (self.namespace + self.identifier).__hash__()

    def __post_init__(self):
        # if a user skips defining a grain, use the defined keys
        if not self.grain or not self.grain.components:
            self.grain = Grain(
                components=[v for v in self.concepts if v.purpose == Purpose.KEY]
            )
        if isinstance(self.address, str):
            self.address = Address(location=self.address)
        if not self.namespace:
            self.namespace = ""

    @property
    def concepts(self) -> List[Concept]:
        return [c.concept for c in self.columns]

    def get_alias(self, concept: Concept, use_raw_name: bool = True):
        for x in self.columns:
            if x.concept.with_grain(concept.grain) == concept:
                if use_raw_name:
                    return x.alias
                return concept.safe_address
        existing = [str(c.concept.with_grain(self.grain)) for c in self.columns]
        raise ValueError(
            f"Concept {concept} not found on {self.identifier}; have {existing}."
        )

    @property
    def name(self):
        return self.identifier

    @property
    def safe_location(self):
        if isinstance(self.address, Address):
            return self.address.location
        return self.address


@dataclass(eq=True)
class JoinedDataSource:
    concepts: List[Concept]
    source_map: Dict[str, "CTE"]
    grain: Grain
    address: Address
    # base: Datasource
    joins: List["Join"]

    @property
    def datasources(self) -> List[Datasource]:
        datasources = []
        for item in self.source_map.values():
            datasources.append(item.source)

        return unique(datasources, "identifier")

    @property
    def identifier(self) -> str:
        return "_join_".join([d.name for d in self.datasources])

    def get_alias(self, concept: Concept):
        for x in self.datasources:
            try:
                return x.get_alias(concept.with_grain(x.grain))
            except ValueError as e:
                from preql.constants import logger

                logger.error(e)
                continue
        existing = [str(c) for c in self.concepts]
        raise ValueError(
            f"Concept {str(concept)} not found on {self.identifier}; have {existing}."
        )


@dataclass()
class BaseJoin:
    left_datasource: Datasource
    right_datasource: Datasource
    concepts: List[Concept]
    join_type: JoinType

    def __str__(self):
        return f'{self.join_type.value} JOIN {self.left_datasource.identifier} and {self.right_datasource.identifier} on {",".join([str(k) for k in self.concepts])}'


@dataclass(eq=True)
class QueryDatasource:
    input_concepts: List[Concept]
    output_concepts: List[Concept]
    source_map: Dict[str, Set[Datasource]]
    datasources: List[Datasource]
    grain: Grain
    joins: List[BaseJoin]

    def __post_init__(self):
        self.output_concepts = unique(self.output_concepts, "address")

    def __add__(self, other):
        if not isinstance(other, QueryDatasource):
            raise ValueError
        if not other.grain == self.grain:
            raise ValueError

        return QueryDatasource(
            input_concepts=self.input_concepts + other.input_concepts,
            output_concepts=self.output_concepts + other.output_concepts,
            source_map={**self.source_map, **other.source_map},
            datasources=self.datasources,
            grain=self.grain,
            joins=self.joins + other.joins,
        )

    @property
    def identifier(self) -> str:
        grain = ",".join([str(c.name) for c in self.grain.components])
        return "_join_".join([d.name for d in self.datasources]) + f"<{grain}>"

    def get_alias(self, concept: Concept):
        # if we should use the raw datasource name to access
        use_raw_name = True if len(self.datasources) == 1 else False
        for x in self.datasources:
            try:
                return x.get_alias(concept.with_grain(self.grain), use_raw_name)
            except ValueError as e:
                from preql.constants import logger

                continue
        existing = [str(c.with_grain(self.grain)) for c in self.output_concepts]
        raise ValueError(
            f"Concept {str(concept)} not found on {self.identifier}; have {existing}."
        )


@dataclass
class Comment:
    text: str


@dataclass
class CTE:
    name: str
    source: "QueryDatasource"  # TODO: make recursive
    # output columns are what are selected/grouped by
    output_columns: List[Concept]
    source_map: Dict[str, str]
    # related columns include all referenced columns, such as filtering
    related_columns: List[Concept]
    grain: Grain
    base: bool = False
    group_to_grain: bool = False
    parent_ctes: List["CTE"] = field(default_factory=list)
    joins: List["Join"] = field(default_factory=list)

    @property
    def base_name(self) -> str:
        if len(self.source.datasources) == 1:
            return self.source.datasources[0].safe_location
        elif self.joins and len(self.joins) > 0:
            return self.joins[0].left_cte.name
        raise ValueError

    @property
    def base_alias(self) -> str:
        if len(self.source.datasources) == 1:
            return self.source.datasources[0].name
        return self.joins[0].left_cte.name

    def get_alias(self, concept: Concept):
        try:
            return self.source.get_alias(concept)
        except ValueError as e:
            raise e
        #
        # for x in self.columns:
        #     if x.concept == concept:
        #         return x.alias
        # existing = [c.concept.name for c in self.columns]
        # raise ValueError(
        #     f"Concept {concept.name} not found on {self.identifier}; have {existing}."
        # )


@dataclass
class CompiledCTE:
    name: str
    statement: str


@dataclass
class JoinKey:
    concept: Concept

    def __str__(self):
        return str(self.concept)


@dataclass
class Join:
    left_cte: CTE
    right_cte: CTE
    jointype: JoinType
    joinkeys: List[JoinKey]

    def __str__(self):
        return f'{self.jointype.value} JOIN {self.left_cte.name} and {self.right_cte.name} on {",".join([str(k) for k in self.joinkeys])}'


@dataclass
class Environment:
    concepts: Dict[str, Concept] = field(default_factory=dict)
    datasources: Dict[str, Datasource] = field(default_factory=dict)
    namespace: Optional[str] = None
    working_path: str = field(default_factory=lambda: os.getcwd())


@dataclass
class Expr:
    name: str = ""

    @property
    def input(self) -> List[Concept]:
        output: List[Concept] = []
        return output

    @property
    def safe_address(self):
        return ""

    @property
    def address(self):
        return ""


@dataclass
class Comparison:
    left: Union[Concept, Expr, "Conditional"]
    right: Union[Concept, Expr, "Conditional"]
    operator: ComparisonOperator

    @property
    def input(self) -> List[Concept]:
        output: List[Concept] = []
        if isinstance(self.left, (Concept, Expr, Conditional)):
            output += self.left.input
        if isinstance(self.right, (Concept, Expr, Conditional)):
            output += self.right.input
        return output


@dataclass
class Conditional:
    left: Union[Concept, Expr, "Conditional"]
    right: Union[Concept, Expr, "Conditional"]
    operator: BooleanOperator

    @property
    def input(self) -> List[Concept]:
        return self.left.input + self.right.input


@dataclass
class WhereClause:
    conditional: Conditional

    @property
    def input(self) -> List[Concept]:
        return self.conditional.input

    @property
    def grain(self) -> Grain:
        output = []
        for item in self.input:
            if item.purpose == Purpose.KEY:
                output.append(item)
            elif item.purpose == Purpose.PROPERTY:
                output += item.grain.components
        return Grain(components=list(set(output)))


# TODO: combine with CTEs
# CTE contains procesed query?
# or CTE references CTE?
@dataclass
class ProcessedQuery:
    output_columns: List[Concept]
    ctes: List[CTE]
    base: CTE
    joins: List[Join]
    grain: Grain
    limit: Optional[int] = None
    where_clause: Optional[WhereClause] = None
    order_by: Optional[OrderBy] = None
    # base:Dataset


@dataclass
class Limit:
    value: int


Concept.update_forward_refs()
