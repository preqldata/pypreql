from __future__ import annotations
import difflib
import os
from copy import deepcopy
from typing import (
    Dict,
    TypeVar,
    List,
    Optional,
    Union,
    Set,
    Any,
    Sequence,
    ValuesView,
    Callable,
    Annotated,
    get_args,
    Generic,
    Tuple,
    Type,
)
from pydantic_core import core_schema
from pydantic.functional_validators import PlainValidator
from pydantic import (
    BaseModel,
    Field,
    ConfigDict,
    field_validator,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
)
from lark.tree import Meta
from pathlib import Path
from preql.constants import logger, DEFAULT_NAMESPACE, ENV_CACHE_NAME, MagicConstants
from preql.core.enums import (
    InfiniteFunctionArgs,
    DataType,
    Purpose,
    JoinType,
    Ordering,
    Modifier,
    FunctionType,
    FunctionClass,
    BooleanOperator,
    ComparisonOperator,
    WindowOrder,
    PurposeLineage,
    SourceType,
    WindowType,
    ConceptSource,
    DatePart,
    ShowCategory,
)
from preql.core.exceptions import UndefinedConceptException
from preql.utility import unique
from collections import UserList

LOGGER_PREFIX = "[MODELS]"

KT = TypeVar("KT")
VT = TypeVar("VT")
LT = TypeVar("LT")


def get_version():
    from preql import __version__

    return __version__


def get_concept_arguments(expr) -> List["Concept"]:
    output = []
    if isinstance(expr, Concept):
        output += [expr]

    elif isinstance(
        expr,
        (
            Comparison,
            Conditional,
            Function,
            Parenthetical,
            AggregateWrapper,
            CaseWhen,
            CaseElse,
        ),
    ):
        output += expr.concept_arguments
    return output


class ListWrapper(Generic[VT], UserList):
    """Used to distinguish parsed list objects from other lists"""

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: Callable[[Any], core_schema.CoreSchema]
    ) -> core_schema.CoreSchema:
        args = get_args(source_type)
        if args:
            schema = handler(List[args])  # type: ignore
        else:
            schema = handler(List)
        return core_schema.no_info_after_validator_function(cls.validate, schema)

    @classmethod
    def validate(cls, v):
        return cls(v)


class Metadata(BaseModel):
    """Metadata container object.
    TODO: support arbitrary tags"""

    description: Optional[str] = None
    line_number: Optional[int] = None
    concept_source: ConceptSource = ConceptSource.MANUAL


def lineage_validator(
    v: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
) -> Union[Function, WindowItem, FilterItem, AggregateWrapper]:
    if v and not isinstance(v, (Function, WindowItem, FilterItem, AggregateWrapper)):
        raise ValueError(v)
    return v


def empty_grain() -> Grain:
    return Grain(components=[])


class Concept(BaseModel):
    name: str
    datatype: DataType
    purpose: Purpose
    metadata: Optional[Metadata] = Field(
        default_factory=lambda: Metadata(description=None, line_number=None),
        validate_default=True,
    )
    lineage: Optional[Union[Function, WindowItem, FilterItem, AggregateWrapper]] = None
    # lineage: Annotated[Optional[
    #     Union[Function, WindowItem, FilterItem, AggregateWrapper]
    # ], WrapValidator(lineage_validator)] = None
    namespace: Optional[str] = Field(default=DEFAULT_NAMESPACE, validate_default=True)
    keys: Optional[List["Concept"]] = None
    grain: "Grain" = Field(default=None, validate_default=True)

    def __hash__(self):
        return hash(str(self))

    @field_validator("namespace", mode="plain")
    @classmethod
    def namespace_validation(cls, v):
        return v or DEFAULT_NAMESPACE

    @field_validator("metadata")
    @classmethod
    def metadata_validation(cls, v):
        v = v or Metadata()
        return v

    @field_validator("grain", mode="before")
    @classmethod
    def parse_grain(cls, v, info: ValidationInfo) -> Grain:
        # this is silly - rethink how we do grains
        values = info.data
        if not v and values.get("purpose", None) == Purpose.KEY:
            v = Grain(
                components=[
                    Concept(
                        namespace=values.get("namespace", DEFAULT_NAMESPACE),
                        name=values["name"],
                        datatype=values["datatype"],
                        purpose=values["purpose"],
                        grain=Grain(),
                    )
                ]
            )
        elif (
            "lineage" in values
            and isinstance(values["lineage"], AggregateWrapper)
            and values["lineage"].by
        ):
            v = Grain(components=values["lineage"].by)
        elif not v:
            v = Grain(components=[])
        elif isinstance(v, Concept):
            v = Grain(components=[v])
        if not v:
            raise SyntaxError(f"Invalid grain {v} for concept {values['name']}")
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
            # and self.keys == other.keys
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
        if self.namespace == DEFAULT_NAMESPACE:
            return self.name.replace(".", "_")
        elif self.namespace:
            return f"{self.namespace.replace('.','_')}_{self.name.replace('.','_')}"
        return self.name.replace(".", "_")

    @property
    def grain_components(self) -> List["Concept"]:
        return self.grain.components_copy if self.grain else []

    def with_namespace(self, namespace: str) -> "Concept":
        return self.__class__(
            name=self.name,
            datatype=self.datatype,
            purpose=self.purpose,
            metadata=self.metadata,
            lineage=self.lineage.with_namespace(namespace) if self.lineage else None,
            grain=self.grain.with_namespace(namespace)
            if self.grain
            else Grain(components=[]),
            namespace=namespace,
            keys=self.keys,
        )

    def with_grain(self, grain: Optional["Grain"] = None) -> "Concept":
        return self.__class__(
            name=self.name,
            datatype=self.datatype,
            purpose=self.purpose,
            metadata=self.metadata,
            lineage=self.lineage,
            grain=grain or Grain(components=[]),
            namespace=self.namespace,
            keys=self.keys,
        )

    def with_default_grain(self) -> "Concept":
        if self.purpose == Purpose.KEY:
            # we need to make this abstract
            grain = Grain(components=[deepcopy(self).with_grain(Grain())], nested=True)
        elif self.purpose == Purpose.PROPERTY:
            components = []
            if self.keys:
                components = self.keys
            if self.lineage:
                for item in self.lineage.arguments:
                    if isinstance(item, Concept):
                        if item.keys and not all(c in components for c in item.keys):
                            components += item.sources
                        else:
                            components += item.sources
            grain = Grain(components=components)
        elif self.purpose == Purpose.METRIC:
            grain = Grain()
        else:
            grain = self.grain  # type: ignore
        return self.__class__(
            name=self.name,
            datatype=self.datatype,
            purpose=self.purpose,
            metadata=self.metadata,
            lineage=self.lineage,
            grain=grain,
            keys=self.keys,
            namespace=self.namespace,
        )

    @property
    def sources(self) -> List["Concept"]:
        if self.lineage:
            output = []
            for item in self.lineage.arguments:
                if isinstance(item, Concept):
                    output.append(item)
                    output += item.sources
            return output
        return []

    @property
    def input(self):
        return [self] + self.sources

    @property
    def derivation(self) -> PurposeLineage:
        if self.lineage and isinstance(self.lineage, WindowItem):
            return PurposeLineage.WINDOW
        elif self.lineage and isinstance(self.lineage, FilterItem):
            return PurposeLineage.FILTER
        elif self.lineage and isinstance(self.lineage, AggregateWrapper):
            return PurposeLineage.AGGREGATE

        elif (
            self.lineage
            and isinstance(self.lineage, Function)
            and self.lineage.operator in FunctionClass.AGGREGATE_FUNCTIONS.value
        ):
            return PurposeLineage.AGGREGATE
        elif (
            self.lineage
            and isinstance(self.lineage, Function)
            and self.lineage.operator == FunctionType.UNNEST
        ):
            return PurposeLineage.UNNEST
        elif self.purpose == Purpose.CONSTANT:
            return PurposeLineage.CONSTANT
        return PurposeLineage.BASIC


class Grain(BaseModel):
    nested: bool = False
    components: List[Concept] = Field(default_factory=list, validate_default=True)

    @field_validator("components")
    def component_nest(cls, v, info: ValidationInfo):
        values = info.data
        if not values.get("nested", False):
            v = [safe_concept(c).with_default_grain() for c in v]
        v = unique(v, "address")
        return v

    @property
    def components_copy(self) -> List[Concept]:
        return deepcopy(self.components)

    def __str__(self):
        if self.abstract:
            return "Grain<Abstract>"
        return "Grain<" + ",".join([c.address for c in self.components]) + ">"

    def with_namespace(self, namespace: str) -> "Grain":
        return Grain(
            components=[c.with_namespace(namespace) for c in self.components],
            nested=self.nested,
        )

    @property
    def abstract(self):
        return not self.components

    @property
    def set(self):
        return set([c.address for c in self.components_copy])

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

    def __add__(self, other: "Grain") -> "Grain":
        components = []
        for clist in [self.components_copy, other.components_copy]:
            for component in clist:
                if component.with_default_grain() in components:
                    continue
                components.append(component.with_default_grain())
        base_components = [c for c in components if c.purpose == Purpose.KEY]
        for c in components:
            if c.purpose == Purpose.PROPERTY and not any(
                [key in base_components for key in (c.keys or [])]
            ):
                base_components.append(c)
        return Grain(components=base_components)

    def __radd__(self, other) -> "Grain":
        if other == 0:
            return self
        else:
            return self.__add__(other)


class RawColumnExpr(BaseModel):
    text: str


class ColumnAssignment(BaseModel):
    alias: str | RawColumnExpr
    concept: Concept
    modifiers: List[Modifier] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return Modifier.PARTIAL not in self.modifiers

    def with_namespace(self, namespace: str) -> "ColumnAssignment":
        # this breaks assignments
        # TODO: figure out why
        return self
        # return ColumnAssignment(
        #     alias=self.alias,
        #     concept=self.concept.with_namespace(namespace),
        #     modifiers=self.modifiers,
        # )


class Statement(BaseModel):
    pass


class Function(BaseModel):
    operator: FunctionType
    arg_count: int = Field(default=1)
    output_datatype: DataType
    output_purpose: Purpose
    valid_inputs: Optional[Union[Set[DataType], List[Set[DataType]]]] = None
    arguments: Sequence[
        Union[
            Concept,
            "AggregateWrapper",
            "Function",
            int,
            float,
            str,
            DataType,
            DatePart,
            "Parenthetical",
            "CaseWhen",
            "CaseElse",
            ListWrapper[int],
            ListWrapper[str],
            ListWrapper[float],
        ]
    ]

    def __str__(self):
        return f'{self.operator.value}({",".join([str(a) for a in self.arguments])})'

    @property
    def datatype(self):
        return self.output_datatype

    @field_validator("arguments")
    @classmethod
    def parse_arguments(cls, v, info: ValidationInfo):
        from preql.parsing.exceptions import ParseError

        values = info.data
        arg_count = len(v)
        target_arg_count = values["arg_count"]
        operator_name = values["operator"].name
        valid_inputs = values["valid_inputs"]
        if not arg_count <= target_arg_count:
            if target_arg_count != InfiniteFunctionArgs:
                raise ParseError(
                    f"Incorrect argument count to {operator_name} function, expects"
                    f" {target_arg_count}, got {arg_count}"
                )
        # if all arguments can be any of the set type
        # turn this into an array for validation
        if isinstance(valid_inputs, set):
            valid_inputs = [valid_inputs for _ in v]
        elif not valid_inputs:
            return v
        for idx, arg in enumerate(v):
            if isinstance(arg, Concept) and arg.datatype not in valid_inputs[idx]:
                if arg.datatype != DataType.UNKNOWN:
                    raise TypeError(
                        f"Invalid input datatype {arg.datatype} passed into"
                        f" {operator_name} from concept {arg.name}"
                    )
            if (
                isinstance(arg, Function)
                and arg.output_datatype not in valid_inputs[idx]
            ):
                if arg.output_datatype != DataType.UNKNOWN:
                    raise TypeError(
                        f"Invalid input datatype {arg.output_datatype} passed into"
                        f" {operator_name} from function {arg.operator.name}"
                    )
            # check constants
            comparisons: List[Tuple[Type, DataType]] = [
                (str, DataType.STRING),
                (int, DataType.INTEGER),
                (float, DataType.FLOAT),
                (bool, DataType.BOOL),
                (DatePart, DataType.DATE_PART),
            ]
            for ptype, dtype in comparisons:
                if isinstance(arg, ptype) and dtype in valid_inputs[idx]:
                    # attempt to exit early to avoid checking all types
                    break
                elif isinstance(arg, ptype):
                    raise TypeError(
                        f"Invalid {dtype} constant passed into {operator_name} {arg}, expecting one of {valid_inputs[idx]}"
                    )
        return v

    def with_namespace(self, namespace: str) -> "Function":
        return Function(
            operator=self.operator,
            arguments=[
                c.with_namespace(namespace) if isinstance(c, Concept) else c
                for c in self.arguments
            ],
            output_datatype=self.output_datatype,
            output_purpose=self.output_purpose,
            valid_inputs=self.valid_inputs,
        )

    @property
    def concept_arguments(self) -> List[Concept]:
        base = []
        for arg in self.arguments:
            base += get_concept_arguments(arg)
        return base

    @property
    def output_grain(self):
        # aggregates have an abstract grain
        base_grain = Grain(components=[])
        if self.operator in FunctionClass.AGGREGATE_FUNCTIONS.value:
            return base_grain
        # scalars have implicit grain of all arguments
        # for input in self.concept_arguments:
        #
        #     base_grain += input.grain
        return base_grain


class ConceptTransform(BaseModel):
    function: Function
    output: Concept
    modifiers: List[Modifier] = Field(default_factory=list)

    @property
    def input(self) -> List[Concept]:
        return [v for v in self.function.arguments if isinstance(v, Concept)]


class Window(BaseModel):
    count: int
    window_order: WindowOrder

    def __str__(self):
        return f"Window<{self.window_order}>"


class WindowItemOver(BaseModel):
    contents: List[Concept]


class WindowItemOrder(BaseModel):
    contents: List["OrderItem"]


class WindowItem(BaseModel):
    type: WindowType
    content: Concept
    order_by: List["OrderItem"]
    over: List["Concept"] = Field(default_factory=list)

    def with_namespace(self, namespace: str) -> "WindowItem":
        return WindowItem(
            type=self.type,
            content=self.content.with_namespace(namespace),
            over=[x.with_namespace(namespace) for x in self.over],
            order_by=[x.with_namespace(namespace) for x in self.order_by],
        )

    @property
    def concept_arguments(self) -> List[Concept]:
        return self.arguments

    @property
    def arguments(self) -> List[Concept]:
        output = [self.content]
        for order in self.order_by:
            output += [order.output]
        for item in self.over:
            output += [item]
        return output

    @property
    def output(self) -> Concept:
        if isinstance(self.content, ConceptTransform):
            return self.content.output
        return self.content

    @output.setter
    def output(self, value):
        if isinstance(self.content, ConceptTransform):
            self.content.output = value
        else:
            self.content = value

    @property
    def input(self) -> List[Concept]:
        base = self.content.input
        for v in self.order_by:
            base += v.input
        for c in self.over:
            base += c.input
        return base

    @property
    def output_datatype(self):
        return self.content.datatype

    @property
    def output_purpose(self):
        return self.content.purpose


class FilterItem(BaseModel):
    content: Concept
    where: "WhereClause"

    def __str__(self):
        return f"<{str(self.content)} {str(self.where)}>"

    def with_namespace(self, namespace: str) -> "FilterItem":
        return FilterItem(
            content=self.content.with_namespace(namespace),
            where=self.where.with_namespace(namespace),
        )

    @property
    def arguments(self) -> List[Concept]:
        output = [self.content]
        output += self.where.input
        return output

    @property
    def output(self) -> Concept:
        if isinstance(self.content, ConceptTransform):
            return self.content.output
        return self.content

    @output.setter
    def output(self, value):
        if isinstance(self.content, ConceptTransform):
            self.content.output = value
        else:
            self.content = value

    @property
    def input(self) -> List[Concept]:
        base = self.content.input
        base += self.where.input
        return base

    @property
    def output_datatype(self):
        return self.content.datatype

    @property
    def output_purpose(self):
        return self.content.purpose

    @property
    def concept_arguments(self):
        return [self.content] + self.where.concept_arguments


class SelectItem(BaseModel):
    content: Union[Concept, ConceptTransform]
    modifiers: List[Modifier] = Field(default_factory=list)

    @property
    def output(self) -> Concept:
        if isinstance(self.content, ConceptTransform):
            return self.content.output
        elif isinstance(self.content, WindowItem):
            return self.content.output
        return self.content

    @property
    def input(self) -> List[Concept]:
        return self.content.input


class OrderItem(BaseModel):
    expr: Concept
    order: Ordering

    def with_namespace(self, namespace: str) -> "OrderItem":
        return OrderItem(expr=self.expr.with_namespace(namespace), order=self.order)

    @property
    def input(self):
        return self.expr.input

    @property
    def output(self):
        return self.expr.output


class OrderBy(BaseModel):
    items: List[OrderItem]


class Select(BaseModel):
    selection: Sequence[Union[SelectItem, Concept, ConceptTransform]]
    where_clause: Optional["WhereClause"] = None
    order_by: Optional[OrderBy] = None
    limit: Optional[int] = None

    def __str__(self):
        from preql.parsing.render import render_query

        return render_query(self)

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
            if isinstance(item, Concept):
                output.append(item)
            elif Modifier.HIDDEN not in item.modifiers:
                output.append(item.output)
        return output

    @property
    def all_components(self) -> List[Concept]:
        return (
            self.input_components + self.output_components + self.grain.components_copy
        )

    @property
    def grain(self) -> "Grain":
        output = []
        for item in self.output_components:
            if item.purpose == Purpose.KEY:
                output.append(item)
        if self.where_clause:
            for item in self.where_clause.concept_arguments:
                if item.purpose == Purpose.KEY:
                    output.append(item)
                # elif item.purpose == Purpose.PROPERTY and item.grain:
                #     output += item.grain.components
            # TODO: handle other grain cases
            # new if block be design
        # add back any purpose that is not at the grain
        # if a query already has the key of the property in the grain
        # we want to group to that grain and ignore the property, which is a derivation
        # otherwise, we need to include property as the group by
        for item in self.output_components:
            if (
                item.purpose == Purpose.PROPERTY
                and item.grain
                and (
                    not item.grain.components
                    or not item.grain.issubset(
                        Grain(components=unique(output, "address"))
                    )
                )
            ):
                output.append(item)
        return Grain(components=unique(output, "address"))


class Address(BaseModel):
    location: str


class Query(BaseModel):
    text: str


def safe_concept(v: Union[Dict, Concept]) -> Concept:
    if isinstance(v, dict):
        return Concept.model_validate(v)
    return v


class GrainWindow(BaseModel):
    window: Window
    sort_concepts: List[Concept]

    def __str__(self):
        return (
            "GrainWindow<"
            + ",".join([c.address for c in self.sort_concepts])
            + f":{str(self.window)}>"
        )


def safe_grain(v):
    if isinstance(v, dict):
        return Grain.parse_obj(v)
    return v


class DatasourceMetadata(BaseModel):
    freshness_concept: Concept | None
    partition_fields: List[Concept] = Field(default_factory=list)


class Datasource(BaseModel):
    identifier: str
    columns: List[ColumnAssignment]
    address: Union[Address, str]
    grain: Grain = Field(
        default_factory=lambda: Grain(components=[]), validate_default=True
    )
    namespace: Optional[str] = Field(default=DEFAULT_NAMESPACE, validate_default=True)
    metadata: DatasourceMetadata = Field(
        default_factory=lambda: DatasourceMetadata(freshness_concept=None)
    )

    @field_validator("namespace", mode="plain")
    @classmethod
    def namespace_validation(cls, v):
        return v or DEFAULT_NAMESPACE

    def add_column(self, concept: Concept, alias: str, modifiers=None):
        self.columns.append(
            ColumnAssignment(alias=alias, concept=concept, modifiers=modifiers)
        )

    @field_validator("address")
    @classmethod
    def address_enforcement(cls, v):
        if isinstance(v, str):
            v = Address(location=v)
        return v

    @field_validator("grain", mode="plain")
    @classmethod
    def grain_enforcement(cls, v: Grain, info: ValidationInfo):
        values = info.data
        v = safe_grain(v)
        if not v or (v and not v.components):
            v = Grain(
                components=[
                    deepcopy(c.concept).with_grain(Grain())
                    for c in values.get("columns", [])
                    if c.concept.purpose == Purpose.KEY
                ]
            )
        return v

    def __add__(self, other):
        if not other == self:
            raise ValueError(
                "Attempted to add two datasources that are not identical, this should"
                " never happen"
            )
        return self

    def __str__(self):
        return f"{self.namespace}.{self.identifier}@<{self.grain}>"

    def __hash__(self):
        return (self.namespace + self.identifier).__hash__()

    def with_namespace(self, namespace: str):
        return Datasource(
            identifier=self.identifier,
            namespace=namespace,
            grain=self.grain.with_namespace(namespace),
            address=self.address,
            columns=[c.with_namespace(namespace) for c in self.columns],
        )

    @property
    def concepts(self) -> List[Concept]:
        return [c.concept for c in self.columns]

    @property
    def group_required(self):
        return False

    @property
    def full_concepts(self) -> List[Concept]:
        return [c.concept for c in self.columns if Modifier.PARTIAL not in c.modifiers]

    @property
    def output_concepts(self) -> List[Concept]:
        return self.concepts

    @property
    def partial_concepts(self) -> List[Concept]:
        return [c.concept for c in self.columns if Modifier.PARTIAL in c.modifiers]

    def get_alias(
        self, concept: Concept, use_raw_name: bool = True, force_alias: bool = False
    ) -> Optional[str | RawColumnExpr]:
        # 2022-01-22
        # this logic needs to be refined.
        # if concept.lineage:
        # #     return None
        for x in self.columns:
            if x.concept == concept or x.concept.with_grain(concept.grain) == concept:
                if use_raw_name:
                    return x.alias
                return concept.safe_address
        existing = [str(c.concept.with_grain(self.grain)) for c in self.columns]
        raise ValueError(
            f"{LOGGER_PREFIX} Concept {concept} not found on {self.identifier}; have"
            f" {existing}."
        )

    @property
    def name(self) -> str:
        return self.identifier
        # TODO: namespace all references
        # return f'{self.namespace}_{self.identifier}'

    @property
    def full_name(self) -> str:
        return f"{self.namespace}_{self.identifier}"

    @property
    def safe_location(self) -> str:
        if isinstance(self.address, Address):
            return self.address.location
        return self.address


class UnnestJoin(BaseModel):
    concept: Concept
    alias: str = "unnest"

    def __hash__(self):
        return (self.alias + self.concept.address).__hash__()


class InstantiatedUnnestJoin(BaseModel):
    concept: Concept
    alias: str = "unnest"


class BaseJoin(BaseModel):
    left_datasource: Union[Datasource, "QueryDatasource"]
    right_datasource: Union[Datasource, "QueryDatasource"]
    concepts: List[Concept]
    join_type: JoinType
    filter_to_mutual: bool = False

    def __init__(self, **data: Any):
        super().__init__(**data)
        if self.left_datasource.full_name == self.right_datasource.full_name:
            raise SyntaxError(
                f"Cannot join a dataself to itself, joining {self.left_datasource} and"
                f" {self.right_datasource}"
            )
        final_concepts = []
        for concept in self.concepts:
            include = True
            for ds in [self.left_datasource, self.right_datasource]:
                if concept.address not in [c.address for c in ds.output_concepts]:
                    if self.filter_to_mutual:
                        include = False
                    else:
                        raise SyntaxError(
                            f"Invalid join, missing {concept} on {ds.name}, have"
                            f" {[c.address for c in ds.output_concepts]}"
                        )
            if include:
                final_concepts.append(concept)
        if not final_concepts and self.concepts:
            # if one datasource only has constants
            # we can join on 1=1
            for ds in [self.left_datasource, self.right_datasource]:
                # constant can be joined at 1=1
                if all([c.purpose == Purpose.CONSTANT for c in ds.output_concepts]):
                    self.concepts = []
                    return
                # if everything is at abstract grain, we can skip joins
                if all([c.grain == Grain() for c in ds.output_concepts]):
                    self.concepts = []
                    return

            left_keys = [c.address for c in self.left_datasource.output_concepts]
            right_keys = [c.address for c in self.right_datasource.output_concepts]
            match_concepts = [c.address for c in self.concepts]
            raise SyntaxError(
                "No mutual join keys found between"
                f" {self.left_datasource.identifier} and"
                f" {self.right_datasource.identifier}, left_keys {left_keys},"
                f" right_keys {right_keys},"
                f" provided join concepts {match_concepts}"
            )
        self.concepts = final_concepts

    @property
    def unique_id(self) -> str:
        # TODO: include join type?
        return (
            self.left_datasource.name
            + self.right_datasource.name
            + self.join_type.value
        )

    def __str__(self):
        return (
            f"{self.join_type.value} JOIN {self.left_datasource.identifier} and"
            f" {self.right_datasource.identifier} on"
            f" {','.join([str(k) for k in self.concepts])}"
        )


class QueryDatasource(BaseModel):
    input_concepts: List[Concept]
    output_concepts: List[Concept]
    source_map: Dict[str, Set[Union[Datasource, "QueryDatasource", "UnnestJoin"]]]
    datasources: Sequence[Union[Datasource, "QueryDatasource"]]
    grain: Grain
    joins: List[BaseJoin | UnnestJoin]
    limit: Optional[int] = None
    condition: Optional[Union["Conditional", "Comparison", "Parenthetical"]] = Field(
        default=None
    )
    filter_concepts: List[Concept] = Field(default_factory=list)
    source_type: SourceType = SourceType.SELECT
    partial_concepts: List[Concept] = Field(default_factory=list)
    join_derived_concepts: List[Concept] = Field(default_factory=list)

    @property
    def non_partial_concept_addresses(self) -> List[str]:
        return [
            c.address
            for c in self.output_concepts
            if c.address not in [z.address for z in self.partial_concepts]
        ]

    @field_validator("input_concepts")
    @classmethod
    def validate_inputs(cls, v):
        return unique(v, "address")

    @field_validator("output_concepts")
    @classmethod
    def validate_outputs(cls, v):
        return unique(v, "address")

    @field_validator("source_map")
    @classmethod
    def validate_source_map(cls, v, info=ValidationInfo):
        values = info.data
        expected = {c.address for c in values["output_concepts"]}.union(
            c.address for c in values["input_concepts"]
        )
        seen = set()
        for k, val in v.items():
            if val:
                if len(val) != 1:
                    raise SyntaxError(f"source map {k} has multiple values {len(val)}")
            seen.add(k)
        if seen != expected:
            raise SyntaxError(
                f"source map has mismatched values: seen {seen},  expected: {expected}"
            )
        return v

    def __str__(self):
        return f"{self.identifier}@<{self.grain}>"

    def __hash__(self):
        return (self.identifier).__hash__()

    @property
    def concepts(self):
        return self.output_concepts

    @property
    def name(self):
        return self.identifier

    @property
    def full_name(self):
        return self.identifier
        # raw = '_'.join([c for c in self.source_map])
        # return raw.replace('.', '_')

    @property
    def group_required(self) -> bool:
        if self.source_type:
            if self.source_type in [SourceType.GROUP, SourceType.FILTER]:
                return True
            elif self.source_type == SourceType.DIRECT_SELECT:
                return (
                    False
                    if sum([ds.grain for ds in self.datasources]) == self.grain
                    else True
                )
        return False

    def __add__(self, other):
        # these are syntax errors to avoid being caught by current
        if not isinstance(other, QueryDatasource):
            raise SyntaxError("Can only merge two query datasources")
        if not other.grain == self.grain:
            raise SyntaxError(
                "Can only merge two query datasources with identical grain"
            )
        if not self.source_type == other.source_type:
            raise SyntaxError(
                "Can only merge two query datasources with identical source type"
            )
        if not self.group_required == other.group_required:
            raise SyntaxError(
                "can only merge two datasources if the group required flag is the same"
            )
        logger.debug(
            f"{LOGGER_PREFIX} merging {self.name} with"
            f" {[c.address for c in self.output_concepts]} concepts and"
            f" {other.name} with {[c.address for c in other.output_concepts]} concepts"
        )

        merged_datasources = {}
        for ds in [*self.datasources, *other.datasources]:
            if ds.full_name in merged_datasources:
                merged_datasources[ds.full_name] = merged_datasources[ds.full_name] + ds
            else:
                merged_datasources[ds.full_name] = ds
        return QueryDatasource(
            input_concepts=unique(
                self.input_concepts + other.input_concepts, "address"
            ),
            output_concepts=unique(
                self.output_concepts + other.output_concepts, "address"
            ),
            source_map={**self.source_map, **other.source_map},
            datasources=list(merged_datasources.values()),
            grain=self.grain,
            joins=unique(self.joins + other.joins, "unique_id"),
            # joins = self.joins,
            condition=(
                self.condition + other.condition
                if (self.condition or other.condition)
                else None
            ),
            source_type=self.source_type,
        )

    @property
    def identifier(self) -> str:
        filters = abs(hash(str(self.condition))) if self.condition else ""
        grain = "_".join(
            [str(c.address).replace(".", "_") for c in self.grain.components]
        )
        return (
            "_join_".join([d.name for d in self.datasources])
            + (f"_at_{grain}" if grain else "_at_abstract")
            + (f"_filtered_by_{filters}" if filters else "")
        )
        # return #str(abs(hash("from_"+"_with_".join([d.name for d in self.datasources]) + ( f"_at_grain_{grain}" if grain else "" ))))

    def get_alias(
        self, concept: Concept, use_raw_name: bool = False, force_alias: bool = False
    ):
        # if we should use the raw datasource name to access
        use_raw_name = (
            True
            if (len(self.datasources) == 1 or use_raw_name) and not force_alias
            # if ((len(self.datasources) == 1 and isinstance(self.datasources[0], Datasource)) or use_raw_name) and not force_alias
            else False
        )
        for x in self.datasources:
            # query datasources should be referenced by their alias, always
            force_alias = isinstance(x, QueryDatasource)
            try:
                return x.get_alias(
                    concept.with_grain(self.grain),
                    use_raw_name,
                    force_alias=force_alias,
                )
            except ValueError as e:
                from preql.constants import logger

                logger.debug(e)
                continue
        existing = [c.with_grain(self.grain) for c in self.output_concepts]
        if concept in existing:
            return concept.name

        existing_str = [str(c) for c in existing]
        datasources = [ds.identifier for ds in self.datasources]
        raise ValueError(
            f"{LOGGER_PREFIX} Concept {str(concept)} not found on {self.identifier};"
            f" have {existing_str} from {datasources}."
        )

    @property
    def safe_location(self):
        return self.identifier


class Comment(BaseModel):
    text: str


class CTE(BaseModel):
    name: str
    source: "QueryDatasource"  # TODO: make recursive
    # output columns are what are selected/grouped by
    output_columns: List[Concept]
    source_map: Dict[str, str]
    grain: Grain
    base: bool = False
    group_to_grain: bool = False
    parent_ctes: List["CTE"] = Field(default_factory=list)
    joins: List[Union["Join", "InstantiatedUnnestJoin"]] = Field(default_factory=list)
    condition: Optional[Union["Conditional", "Comparison", "Parenthetical"]] = None
    partial_concepts: List[Concept] = Field(default_factory=list)
    join_derived_concepts: List[Concept] = Field(default_factory=list)

    @field_validator("output_columns")
    def validate_output_columns(cls, v):
        return unique(v, "address")

    def __add__(self, other: "CTE"):
        logger.info('Merging two copies of CTE "%s"', self.name)
        if not self.grain == other.grain:
            error = (
                "Attempting to merge two ctes of different grains"
                f" {self.name} {other.name} grains {self.grain} {other.grain}"
            )
            raise ValueError(error)
        self.partial_concepts = unique(
            self.partial_concepts + other.partial_concepts, "address"
        )
        self.parent_ctes = merge_ctes(self.parent_ctes + other.parent_ctes)

        self.source_map = {**self.source_map, **other.source_map}

        self.output_columns = unique(
            self.output_columns + other.output_columns, "address"
        )
        self.joins = unique(self.joins + other.joins, "unique_id")
        self.partial_concepts = unique(
            self.partial_concepts + other.partial_concepts, "address"
        )
        self.join_derived_concepts = unique(
            self.join_derived_concepts + other.join_derived_concepts, "address"
        )

        self.source.source_map = {**self.source.source_map, **other.source.source_map}
        self.source.output_concepts = unique(
            self.source.output_concepts + other.source.output_concepts, "address"
        )
        return self

    @property
    def relevant_base_ctes(self):
        """The parent CTEs includes all CTES,
        not just those immediately referenced.
        This method returns only those that are relevant
        to the output of the query."""
        return self.parent_ctes

    @property
    def base_name(self) -> str:
        # if this cte selects from a single datasource, select right from it
        valid_joins: List[Join] = [
            join for join in self.joins if isinstance(join, Join)
        ]
        if len(self.source.datasources) == 1 and isinstance(
            self.source.datasources[0], Datasource
        ):
            return self.source.datasources[0].safe_location
        # if we have multiple joined CTEs, pick the base
        # as the root
        elif valid_joins and len(valid_joins) > 0:
            return valid_joins[0].left_cte.name
        elif self.relevant_base_ctes:
            return self.relevant_base_ctes[0].name
        # return self.source_map.values()[0]
        elif self.parent_ctes:
            raise SyntaxError(
                f"{self.name} has no relevant base CTEs, {self.source_map},"
                f" {[x.name for x in self.parent_ctes]}, outputs"
                f" {[x.address for x in self.output_columns]}"
            )
        return self.source.name

    @property
    def base_alias(self) -> str:
        relevant_joins = [j for j in self.joins if isinstance(j, Join)]
        if len(self.source.datasources) == 1 and isinstance(
            self.source.datasources[0], Datasource
        ):
            # if isinstance(self.source.datasources[0], QueryDatasource) and self.relevant_base_ctes:
            #     return self.relevant_base_ctes[0].name
            return self.source.datasources[0].full_name.replace(".", "_")
        if relevant_joins:
            return relevant_joins[0].left_cte.name
        elif self.relevant_base_ctes:
            return self.relevant_base_ctes[0].name
        elif self.parent_ctes:
            return self.parent_ctes[0].name
        return self.name

    def get_alias(self, concept: Concept) -> str:
        for cte in self.parent_ctes:
            if concept.address in [x.address for x in cte.output_columns]:
                return concept.safe_address
        try:
            return self.source.get_alias(concept)
        except ValueError as e:
            return f"INVALID_ALIAS: {str(e)}"

    @property
    def render_from_clause(self) -> bool:
        if (
            all([c.purpose == Purpose.CONSTANT for c in self.output_columns])
            and not self.parent_ctes
            and not self.group_to_grain
        ):
            return False
        return True

    @property
    def sourced_concepts(self) -> List[Concept]:
        return [c for c in self.output_columns if c.address in self.source_map]


def merge_ctes(ctes: List[CTE]) -> List[CTE]:
    final_ctes_dict: Dict[str, CTE] = {}
    # merge CTEs
    for cte in ctes:
        if cte.name not in final_ctes_dict:
            final_ctes_dict[cte.name] = cte
        else:
            final_ctes_dict[cte.name] = final_ctes_dict[cte.name] + cte

    final_ctes = list(final_ctes_dict.values())
    return final_ctes


class CompiledCTE(BaseModel):
    name: str
    statement: str


class JoinKey(BaseModel):
    concept: Concept

    def __str__(self):
        return str(self.concept)


class Join(BaseModel):
    left_cte: CTE
    right_cte: CTE
    jointype: JoinType
    joinkeys: List[JoinKey]

    @property
    def unique_id(self) -> str:
        return self.left_cte.name + self.right_cte.name + self.jointype.value

    def __str__(self):
        return (
            f"{self.jointype.value} JOIN {self.left_cte.name} and"
            f" {self.right_cte.name} on {','.join([str(k) for k in self.joinkeys])}"
        )


class UndefinedConcept(Concept):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str
    environment: "EnvironmentConceptDict"
    line_no: int | None = None
    datatype: DataType = DataType.UNKNOWN
    purpose: Purpose = Purpose.AUTO

    def with_namespace(self, namespace: str) -> "UndefinedConcept":
        return self.__class__(
            name=self.name,
            datatype=self.datatype,
            purpose=self.purpose,
            metadata=self.metadata,
            lineage=self.lineage.with_namespace(namespace) if self.lineage else None,
            grain=self.grain.with_namespace(namespace)
            if self.grain
            else Grain(components=[]),
            namespace=namespace,
            keys=self.keys,
            environment=self.environment,
            line_no=self.line_no,
        )

    def with_grain(self, grain: Optional["Grain"] = None) -> "Concept":
        return self.__class__(
            name=self.name,
            datatype=self.datatype,
            purpose=self.purpose,
            metadata=self.metadata,
            lineage=self.lineage,
            grain=grain or Grain(components=[]),
            namespace=self.namespace,
            keys=self.keys,
            environment=self.environment,
            line_no=self.line_no,
        )

    def with_default_grain(self) -> "Concept":
        if self.purpose == Purpose.KEY:
            # we need to make this abstract
            grain = Grain(components=[deepcopy(self).with_grain(Grain())], nested=True)
        elif self.purpose == Purpose.PROPERTY:
            components = []
            if self.keys:
                components = self.keys
            if self.lineage:
                for item in self.lineage.arguments:
                    if isinstance(item, Concept):
                        if item.keys and not all(c in components for c in item.keys):
                            components += item.sources
                        else:
                            components += item.sources
            grain = Grain(components=components)
        elif self.purpose == Purpose.METRIC:
            grain = Grain()
        else:
            grain = self.grain  # type: ignore
        return self.__class__(
            name=self.name,
            datatype=self.datatype,
            purpose=self.purpose,
            metadata=self.metadata,
            lineage=self.lineage,
            grain=grain,
            keys=self.keys,
            namespace=self.namespace,
            environment=self.environment,
            line_no=self.line_no,
        )


class EnvironmentConceptDict(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(self, *args, **kwargs)
        self.undefined: dict[str, UndefinedConcept] = {}
        self.fail_on_missing: bool = False
        self.populate_default_concepts()

    def populate_default_concepts(self):
        from preql.core.internal import DEFAULT_CONCEPTS

        for concept in DEFAULT_CONCEPTS.values():
            self[concept.address] = concept

    def values(self) -> ValuesView[Concept]:  # type: ignore
        return super().values()

    def __getitem__(
        self, key, line_no: int | None = None
    ) -> Concept | UndefinedConcept:
        try:
            return super(EnvironmentConceptDict, self).__getitem__(key)

        except KeyError:
            if not self.fail_on_missing:
                undefined = UndefinedConcept(
                    name=key,
                    line_no=line_no,
                    environment=self,
                    datatype=DataType.UNKNOWN,
                    purpose=Purpose.AUTO,
                )
                self.undefined[key] = undefined
                return undefined

            matches = self._find_similar_concepts(key)
            message = f"Undefined concept: {key}."
            if matches:
                message += f" Suggestions: {matches}"

            if line_no:
                raise UndefinedConceptException(f"line: {line_no}: " + message, matches)
            raise UndefinedConceptException(message, matches)

    def _find_similar_concepts(self, concept_name):
        matches = difflib.get_close_matches(concept_name, self.keys())
        return matches


class Import(BaseModel):
    alias: str
    path: str
    # environment: "Environment" | None = None
    # TODO: this might result in a lot of duplication
    # environment:"Environment"


class EnvironmentOptions(BaseModel):
    allow_duplicate_declaration: bool = True


def validate_concepts(v) -> EnvironmentConceptDict:
    if isinstance(v, EnvironmentConceptDict):
        return v
    elif isinstance(v, dict):
        return EnvironmentConceptDict(
            **{x: Concept.model_validate(y) for x, y in v.items()}
        )
    raise ValueError


class Environment(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, strict=False)

    concepts: Annotated[
        EnvironmentConceptDict, PlainValidator(validate_concepts)
    ] = Field(default_factory=EnvironmentConceptDict)
    datasources: Dict[str, Datasource] = Field(default_factory=dict)
    imports: Dict[str, Import] = Field(default_factory=dict)
    namespace: Optional[str] = None
    working_path: str | Path = Field(default_factory=lambda: os.getcwd())
    environment_config: EnvironmentOptions = Field(default_factory=EnvironmentOptions)
    version: str = Field(default_factory=get_version)

    @classmethod
    def from_cache(cls, path) -> Optional["Environment"]:
        with open(path, "r") as f:
            read = f.read()
        base = cls.model_validate_json(read)
        version = get_version()
        if base.version != version:
            return None
        return base

    def to_cache(self, path: Optional[str | Path] = None) -> Path:
        if not path:
            ppath = Path(self.working_path) / ENV_CACHE_NAME
        else:
            ppath = Path(path)
        with open(ppath, "w") as f:
            f.write(self.json())
        return ppath

    @property
    def materialized_concepts(self) -> List[Concept]:
        output = []
        for concept in self.concepts.values():
            found = False
            # basic concepts are effectively materialized
            # and can be found via join paths
            for datasource in self.datasources.values():
                if concept.address in [x.address for x in datasource.output_concepts]:
                    found = True
                    break
            if found:
                output.append(concept)
        return output

    def validate_concept(self, lookup: str, meta: Meta | None = None):
        existing: Concept = self.concepts.get(lookup)  # type: ignore
        if not existing:
            return
        elif existing and self.environment_config.allow_duplicate_declaration:
            return
        elif existing.metadata:
            # if the existing concept is auto derived, we can overwrite it
            if existing.metadata.concept_source == ConceptSource.AUTO_DERIVED:
                return
        elif meta and existing.metadata:
            raise ValueError(
                f"Assignment to concept '{lookup}' on line {meta.line} is a duplicate"
                f" declaration; '{lookup}' was originally defined on line"
                f" {existing.metadata.line_number}"
            )
        elif existing.metadata:
            raise ValueError(
                f"Assignment to concept '{lookup}'  is a duplicate declaration;"
                f" '{lookup}' was originally defined on line"
                f" {existing.metadata.line_number}"
            )
        raise ValueError(
            f"Assignment to concept '{lookup}'  is a duplicate declaration;"
        )

    def parse(self, input: str, namespace: str | None = None):
        from preql import parse

        if namespace:
            new = Environment(namespace=namespace)
            new.parse(input)
            for key, concept in new.concepts.items():
                if not isinstance(concept, Concept):
                    raise SyntaxError(
                        f"Parsed environment {namespace} has invalid concept {type(concept)}"
                    )
                self.concepts[f"{namespace}.{key}"] = concept
            for key, datasource in new.datasources.items():
                self.datasources[f"{namespace}.{key}"] = datasource
            return self
        parse(input, self)
        return self

    def add_concept(
        self,
        concept: Concept,
        meta: Meta | None = None,
        force: bool = False,
        add_derived: bool = True,
    ):
        if not force:
            self.validate_concept(concept.address, meta=meta)
        if (
            concept.namespace == DEFAULT_NAMESPACE
            or concept.namespace == self.namespace
        ):
            self.concepts[concept.name] = concept
        else:
            self.concepts[concept.address] = concept
        if add_derived:
            from preql.core.environment_helpers import generate_related_concepts

            generate_related_concepts(concept, self)
        return concept

    def add_datasource(
        self,
        datasource: Datasource,
    ):
        self.datasources[datasource.identifier] = datasource
        return datasource


# class Expr(BaseModel):
#     content: Any

#     def __init__(self):
#         raise SyntaxError

#     @property
#     def input(self) -> List[Concept]:
#         output: List[Concept] = []
#         return output

#     @property
#     def safe_address(self):
#         return ""

#     @property
#     def address(self):
#         return ""


class Comparison(BaseModel):
    left: Union[
        int,
        str,
        float,
        list,
        bool,
        Function,
        Concept,
        "Conditional",
        DataType,
        "Comparison",
        "Parenthetical",
        MagicConstants,
    ]
    right: Union[
        int,
        str,
        float,
        list,
        bool,
        Concept,
        Function,
        "Conditional",
        DataType,
        "Comparison",
        "Parenthetical",
        MagicConstants,
    ]
    operator: ComparisonOperator

    def __post_init__(self):
        if arg_to_datatype(self.left) != arg_to_datatype(self.right):
            raise ValueError(
                f"Cannot compare {self.left} and {self.right} of different types"
            )

    def __add__(self, other):
        if not isinstance(other, (Comparison, Conditional, Parenthetical)):
            raise ValueError("Cannot add Comparison to non-Comparison")
        if other == self:
            return self
        return Conditional(left=self, right=other, operator=BooleanOperator.AND)

    def __repr__(self):
        return f"{str(self.left)} {self.operator.value} {str(self.right)}"

    def with_namespace(self, namespace: str):
        return Comparison(
            left=(
                self.left.with_namespace(namespace)
                if isinstance(
                    self.left, (Concept, Function, Conditional, Parenthetical)
                )
                else self.left
            ),
            right=(
                self.right.with_namespace(namespace)
                if isinstance(
                    self.right, (Concept, Function, Conditional, Parenthetical)
                )
                else self.right
            ),
            operator=self.operator,
        )

    @property
    def input(self) -> List[Concept]:
        output: List[Concept] = []
        if isinstance(self.left, (Concept,)):
            output += [self.left]
        if isinstance(self.left, (Concept, Conditional, Parenthetical)):
            output += self.left.input
        if isinstance(self.right, (Concept,)):
            output += [self.right]
        if isinstance(self.right, (Concept, Conditional, Parenthetical)):
            output += self.right.input
        if isinstance(self.left, Function):
            output += self.left.concept_arguments
        if isinstance(self.right, Function):
            output += self.right.concept_arguments
        return output

    @property
    def concept_arguments(self) -> List[Concept]:
        """Return concepts directly referenced in where clause"""
        output = []
        output += get_concept_arguments(self.left)
        output += get_concept_arguments(self.right)
        return output


class CaseWhen(BaseModel):
    comparison: Comparison
    expr: "Expr"

    @property
    def concept_arguments(self):
        return get_concept_arguments(self.comparison) + get_concept_arguments(self.expr)


class CaseElse(BaseModel):
    expr: "Expr"

    @property
    def concept_arguments(self):
        return get_concept_arguments(self.expr)


class Conditional(BaseModel):
    left: Union[
        int, str, float, list, bool, Concept, Comparison, "Conditional", "Parenthetical"
    ]
    right: Union[
        int, str, float, list, bool, Concept, Comparison, "Conditional", "Parenthetical"
    ]
    operator: BooleanOperator

    def __add__(self, other) -> "Conditional":
        if other is None:
            return self
        elif isinstance(other, (Comparison, Conditional, Parenthetical)):
            return Conditional(left=self, right=other, operator=BooleanOperator.AND)
        raise ValueError(f"Cannot add {self.__class__} and {type(other)}")

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return f"{str(self.left)} {self.operator.value} {str(self.right)}"

    def with_namespace(self, namespace: str):
        return Conditional(
            left=(
                self.left.with_namespace(namespace)
                if isinstance(
                    self.left, (Concept, Comparison, Conditional, Parenthetical)
                )
                else self.left
            ),
            right=(
                self.right.with_namespace(namespace)
                if isinstance(
                    self.right, (Concept, Comparison, Conditional, Parenthetical)
                )
                else self.right
            ),
            operator=self.operator,
        )

    @property
    def input(self) -> List[Concept]:
        """Return concepts directly referenced in where clause"""
        output = []
        if isinstance(self.left, Concept):
            output += self.input
        elif isinstance(self.left, (Comparison, Conditional)):
            output += self.left.input
        if isinstance(self.right, Concept):
            output += self.right.input
        elif isinstance(self.right, (Comparison, Conditional)):
            output += self.right.input
        if isinstance(self.left, (Function, Parenthetical)):
            output += self.left.concept_arguments
        elif isinstance(self.right, (Function, Parenthetical)):
            output += self.right.concept_arguments
        return output

    @property
    def concept_arguments(self) -> List[Concept]:
        """Return concepts directly referenced in where clause"""
        output = []
        output += get_concept_arguments(self.left)
        output += get_concept_arguments(self.right)
        return output


class AggregateWrapper(BaseModel):
    function: Function
    by: List[Concept] = Field(default_factory=list)

    def __str__(self):
        grain_str = [str(c) for c in self.by] if self.by else "abstract"
        return f"{str(self.function)}<{grain_str}>"

    @property
    def datatype(self):
        return self.function.datatype

    @property
    def concept_arguments(self) -> List[Concept]:
        return self.function.concept_arguments + self.by

    @property
    def output_datatype(self):
        return self.function.output_datatype

    @property
    def output_purpose(self):
        return self.function.output_purpose

    @property
    def arguments(self):
        return self.function.arguments

    def with_namespace(self, namespace: str) -> "AggregateWrapper":
        return AggregateWrapper(
            function=self.function.with_namespace(namespace),
            by=[c.with_namespace(namespace) for c in self.by] if self.by else [],
        )


class WhereClause(BaseModel):
    conditional: Union[Comparison, Conditional, "Parenthetical"]

    @property
    def input(self) -> List[Concept]:
        return self.conditional.input

    @property
    def concept_arguments(self) -> List[Concept]:
        return self.conditional.concept_arguments

    def with_namespace(self, namespace: str):
        return WhereClause(conditional=self.conditional.with_namespace(namespace))

    @property
    def grain(self) -> Grain:
        output = []
        for item in self.input:
            if item.purpose == Purpose.KEY:
                output.append(item)
            elif item.purpose == Purpose.PROPERTY:
                output += item.grain.components if item.grain else []
        return Grain(components=list(set(output)))


class MaterializedDataset(BaseModel):
    address: Address


# TODO: combine with CTEs
# CTE contains procesed query?
# or CTE references CTE?


class ProcessedQuery(BaseModel):
    output_columns: List[Concept]
    ctes: List[CTE]
    base: CTE
    joins: List[Join]
    grain: Grain
    limit: Optional[int] = None
    where_clause: Optional[WhereClause] = None
    order_by: Optional[OrderBy] = None


class ProcessedQueryMixin(BaseModel):
    output_to: MaterializedDataset
    datasource: Datasource
    # base:Dataset


class ProcessedQueryPersist(ProcessedQuery, ProcessedQueryMixin):
    pass


class ProcessedShowStatement(BaseModel):
    output_columns: List[Concept]
    output_values: List[Union[Concept, Datasource, ProcessedQuery]]


class Limit(BaseModel):
    count: int


class ConceptDeclaration(BaseModel):
    concept: Concept


class Parenthetical(BaseModel):
    content: "Expr"
    # Union[
    #     int, str, float, list, bool, Concept, Comparison, "Conditional", "Parenthetical"
    # ]

    def __str__(self):
        return self.__repr__()

    def __add__(self, other) -> Union["Parenthetical", "Conditional"]:
        if other is None:
            return self
        elif isinstance(other, (Comparison, Conditional, Parenthetical)):
            return Conditional(left=self, right=other, operator=BooleanOperator.AND)
        raise ValueError(f"Cannot add {self.__class__} and {type(other)}")

    def __repr__(self):
        return f"({str(self.content)})"

    def with_namespace(self, namespace: str):
        return Parenthetical(
            content=(
                self.content.with_namespace(namespace)
                if hasattr(self.content, "with_namespace")
                else self.content
            )
        )

    @property
    def concept_arguments(self) -> List[Concept]:
        base: List[Concept] = []
        x = self.content
        if hasattr(x, "concept_arguments"):
            base += x.concept_arguments
        elif isinstance(x, Concept):
            base.append(x)
        return base

    @property
    def input(self):
        base = []
        x = self.content
        if hasattr(x, "input"):
            base += x.input
        return base


class Persist(BaseModel):
    datasource: Datasource
    select: Select

    @property
    def identifier(self):
        return self.datasource.identifier

    @property
    def address(self):
        return self.datasource.address


class ShowStatement(BaseModel):
    content: Select | Persist | ShowCategory


Expr = (
    bool
    | int
    | str
    | float
    | list
    | Concept
    | Comparison
    | Conditional
    | Parenthetical
    | Function
    | AggregateWrapper
)


Concept.model_rebuild()
Grain.model_rebuild()
WindowItem.model_rebuild()
WindowItemOrder.model_rebuild()
FilterItem.model_rebuild()
Comparison.model_rebuild()
Conditional.model_rebuild()
Parenthetical.model_rebuild()
WhereClause.model_rebuild()
Import.model_rebuild()
CaseWhen.model_rebuild()
CaseElse.model_rebuild()
Select.model_rebuild()
CTE.model_rebuild()
BaseJoin.model_rebuild()
QueryDatasource.model_rebuild()
ProcessedQuery.model_rebuild()
ProcessedQueryPersist.model_rebuild()
InstantiatedUnnestJoin.model_rebuild()
UndefinedConcept.model_rebuild()
Function.model_rebuild()
Grain.model_rebuild()


def arg_to_datatype(arg) -> DataType:
    if isinstance(arg, Function):
        return arg.output_datatype
    elif isinstance(arg, Concept):
        return arg.datatype
    elif isinstance(arg, bool):
        return DataType.BOOL
    elif isinstance(arg, int):
        return DataType.INTEGER
    elif isinstance(arg, str):
        return DataType.STRING
    elif isinstance(arg, float):
        return DataType.FLOAT
    elif isinstance(arg, ListWrapper):
        return DataType.ARRAY
    elif isinstance(arg, AggregateWrapper):
        return arg.function.output_datatype
    elif isinstance(arg, Parenthetical):
        return arg_to_datatype(arg.content)
    else:
        raise ValueError(f"Cannot parse arg type for {arg} type {type(arg)}")
