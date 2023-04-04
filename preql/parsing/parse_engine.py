from os.path import join, dirname
from typing import Tuple, List, Optional

from lark import Lark, Transformer, v_args
from lark.exceptions import (
    VisitError,
    UnexpectedCharacters,
    UnexpectedEOF,
    UnexpectedInput,
    UnexpectedToken,
)
from lark.tree import Meta
from pydantic import ValidationError

from preql.constants import DEFAULT_NAMESPACE
from preql.core.enums import (
    Purpose,
    DataType,
    Modifier,
    Ordering,
    FunctionType,
    ComparisonOperator,
    BooleanOperator,
    WindowOrder,
    WindowType,
)
from preql.core.exceptions import UndefinedConceptException, InvalidSyntaxException
from preql.core.models import (
    WhereClause,
    Comparison,
    Conditional,
    Comment,
    Datasource,
    Concept,
    ColumnAssignment,
    Select,
    Address,
    Grain,
    SelectItem,
    ConceptTransform,
    Function,
    OrderItem,
    Environment,
    Limit,
    OrderBy,
    Metadata,
    Window,
    WindowItem,
    WindowItemOver,
    WindowItemOrder,
    FilterItem,
    Query,
)
from preql.parsing.exceptions import ParseError

grammar = r"""
    !start: ( block | comment )*
    block: statement _TERMINATOR comment?
    ?statement: concept
    | datasource
    | select
    | import_statement
    
    _TERMINATOR:  ";"i /\s*/
    
    comment:   /#.*(\n|$)/ |  /\/\/.*\n/  
    
    // property display_name string
    concept_declaration: PURPOSE IDENTIFIER TYPE metadata?
    //customer_id.property first_name STRING;
    concept_property_declaration: PROPERTY IDENTIFIER TYPE metadata?
    //metric post_length <- len(post_text);
    concept_derivation:  (PURPOSE | PROPERTY) IDENTIFIER "<" "-" expr
    concept:  concept_declaration | concept_derivation | concept_property_declaration
    
    // datasource concepts
    datasource: "datasource" IDENTIFIER  "("  column_assignment_list ")"  grain_clause? (address | query)
    
    grain_clause: "grain" "(" column_list ")"
    
    address: "address" IDENTIFIER
    
    query: "query" MULTILINE_STRING
    
    concept_assignment: IDENTIFIER | (MODIFIER "[" concept_assignment "]" )
    
    column_assignment: (IDENTIFIER ":" concept_assignment) 
    
    column_assignment_list : (column_assignment "," )* column_assignment ","?
    
    column_list : (IDENTIFIER "," )* IDENTIFIER ","?
    
    import_statement: "import" (IDENTIFIER ".") * IDENTIFIER "as" IDENTIFIER
    
    // select statement
    select: "select"i select_list  where? comment* order_by? comment* limit? comment*
    
    // user_id where state = Mexico
    filter_item: "filter"i IDENTIFIER where
    
    // rank/lag/lead
    WINDOW_TYPE: ("rank"i|"lag"i|"lead"i)  /[\s]+/
    
    window_item: WINDOW_TYPE (IDENTIFIER | select_transform | comment+ ) window_item_over? window_item_order?
    
    window_item_over: ("OVER"i over_list)
    
    window_item_order: ("ORDER"i? "BY"i order_list)
    
    select_item: (IDENTIFIER | select_transform | comment+ ) | ("~" select_item)
    
    select_list:  ( select_item "," )* select_item ","?
    
    //  count(post_id) -> post_count
    select_transform : expr "-" ">" IDENTIFIER metadata?
    
    metadata: "metadata" "(" IDENTIFIER "=" _string_lit ")"
    
    limit: "LIMIT"i /[0-9]+/
    
    !window_order: ("TOP"i | "BOTTOM"i)
    
    window: window_order /[0-9]+/
    
    window_order_by: "BY"i column_list
    
    order_list: (expr ORDERING "," )* expr ORDERING ","?
    
    over_list: (IDENTIFIER "," )* IDENTIFIER ","?
    
    ORDERING: ("ASC"i | "DESC"i)
    
    order_by: "ORDER"i "BY"i order_list
    
    //WHERE STATEMENT
    
    LOGICAL_OPERATOR: "AND"i | "OR"i
    
    conditional: expr LOGICAL_OPERATOR (conditional | expr)
    
    where: "WHERE"i (expr | conditional)
    
    expr_reference: IDENTIFIER
    
    COMPARISON_OPERATOR: ("=" | ">" | "<" | ">=" | "<" | "!=" | "is"i | "in"i )
    
    comparison: expr COMPARISON_OPERATOR expr
    
    expr_tuple: "("  (expr ",")* expr ","?  ")"
    
    in_comparison: expr "in" expr_tuple
    
    expr: window_item | filter_item | fcast | _aggregate_functions | len | _string_functions | _math_functions | concat | _date_functions | in_comparison | comparison | literal |  expr_reference
    
    // functions
    
    //math TODO: add syntactic sugar
    fadd: ("add"i "(" expr "," expr ")" ) | ( expr "+" expr )
    fsub: ("subtract"i "(" expr "," expr ")" ) | ( expr "-" expr )
    fmul: ("multiply"i "(" expr "," expr ")" ) | ( expr "*" expr )
    fdiv: ( "divide"i "(" expr "," expr ")") | ( expr "/" expr )
    fround: "round"i "(" expr "," expr ")"
    
    _math_functions: fadd | fsub | fmul | fdiv | fround
    
    //generic
    fcast: "cast"i "(" expr "AS"i TYPE ")"
    concat: "concat"i "(" (expr ",")* expr ")"
    len: "len"i "(" expr ")"
    
    //string
    like: "like"i "(" expr "," _string_lit ")"
    ilike: "ilike"i "(" expr "," _string_lit ")"
    upper: "upper"i "(" expr ")"
    lower: "lower"i "(" expr ")"    
    
    _string_functions: like | ilike | upper | lower
    
    //aggregates
    count: "count"i "(" expr ")"
    count_distinct: "count_distinct"i "(" expr ")"
    sum: "sum"i "(" expr ")"
    avg: "avg"i "(" expr ")"
    max: "max"i "(" expr ")"
    min: "min"i "(" expr ")"
    
    _aggregate_functions: count | count_distinct | sum | avg | max | min

    // date functions
    fdate: "date"i "(" expr ")"
    fdatetime: "datetime"i "(" expr ")"
    ftimestamp: "timestamp"i "(" expr ")"
    
    fsecond: "second"i "(" expr ")"
    fminute: "minute"i "(" expr ")"
    fhour: "hour"i "(" expr ")"
    fday: "day"i "(" expr ")"
    fweek: "week"i "(" expr ")"
    fmonth: "month"i "(" expr ")"
    fquarter: "quarter"i  "(" expr ")"
    fyear: "year"i "(" expr ")"
    
    fdate_part: "date_part"i "(" expr ")"
    
    _date_functions: fdate | fdatetime | ftimestamp | fsecond | fminute | fhour | fday | fweek | fmonth | fquarter | fyear | fdate_part
    
    // base language constructs
    IDENTIFIER : /[a-zA-Z_][a-zA-Z0-9_\\-\\.\-]*/
    
    MULTILINE_STRING: /\'{3}(.*?)\'{3}/s
    
    DOUBLE_STRING_CHARS: /(?:(?!\${)([^"\\]|\\.))+/+ // any character except "
    SINGLE_STRING_CHARS: /(?:(?!\${)([^'\\]|\\.))+/+ // any character except '
    _single_quote: "'" ( SINGLE_STRING_CHARS )* "'" 
    _double_quote: "\"" ( DOUBLE_STRING_CHARS )* "\"" 
    _string_lit: _single_quote | _double_quote
    
    int_lit: /[0-9]+/
    
    float_lit: /[0-9]+\.[0-9]+/
    
    !bool_lit: "True"i | "False"i
    
    literal: _string_lit | int_lit | float_lit | bool_lit

    MODIFIER: "Optional"i | "Partial"i
    
    TYPE: "string"i | "number"i | "bool"i | "map"i | "list"i | "any"i | "int"i | "date"i | "datetime"i | "timestamp"i | "float"i
    
    PURPOSE:  "key" | "metric"
    PROPERTY: "property"

    %import common.WS_INLINE -> _WHITESPACE
    %import common.WS
    %ignore WS
"""

PARSER = Lark(grammar, start="start", propagate_positions=True)


def parse_concept_reference(
    name: str, environment: Environment
) -> Tuple[str, str, str]:
    if "." in name:
        namespace, name = name.rsplit(".", 1)
        lookup = f"{namespace}.{name}"
    else:
        namespace = environment.namespace or DEFAULT_NAMESPACE
        lookup = name
    return lookup, namespace, name


def arg_to_datatype(arg) -> DataType:
    if isinstance(arg, Function):
        return arg.output_datatype
    elif isinstance(arg, Concept):
        return arg.datatype
    elif isinstance(arg, int):
        return DataType.INTEGER
    elif isinstance(arg, str):
        return DataType.STRING
    elif isinstance(arg, float):
        return DataType.FLOAT
    else:
        raise ValueError(f"Cannot parse arg type for {arg} type {type(arg)}")


class ParseToObjects(Transformer):
    def __init__(self, visit_tokens, text, environment: Environment):
        Transformer.__init__(self, visit_tokens)
        self.text = text
        self.environment = environment

    def validate_concept(self, lookup: str, meta: Meta):
        existing = self.environment.concepts.get(lookup)
        if existing:
            raise ParseError(
                f"Assignment to concept '{lookup}' on line {meta.line} is a duplicate declaration; '{lookup}' was originally defined on line {existing.metadata.line_number}"
            )

    def start(self, args):
        return args

    def block(self, args):
        output = args[0]
        if isinstance(output, Concept):
            if len(args) > 1 and isinstance(args[1], Comment):
                output.metadata.description = (
                    output.metadata.description or args[1].text.split("#")[1].strip()
                )
        return args[0]

    def metadata(self, args):
        pairs = {key: val for key, val in zip(args[::2], args[1::2])}
        return Metadata(**pairs)

    def IDENTIFIER(self, args) -> str:
        return args.value

    def STRING_CHARS(self, args) -> str:
        return args.value

    def SINGLE_STRING_CHARS(self, args) -> str:
        return args.value

    def DOUBLE_STRING_CHARS(self, args) -> str:
        return args.value

    def TYPE(self, args) -> DataType:
        return DataType(args.lower())

    def COMPARISON_OPERATOR(self, args) -> ComparisonOperator:
        return ComparisonOperator(args)

    def LOGICAL_OPERATOR(self, args) -> BooleanOperator:
        return BooleanOperator(args.lower())

    def concept_assignment(self, args):
        return args

    @v_args(meta=True)
    def column_assignment(self, meta: Meta, args):
        # TODO -> deal with conceptual modifiers
        modifiers = []
        concept = args[1]
        # recursively collect modifiers
        while len(concept) > 1:
            modifiers.append(concept[0])
            concept = concept[1]
        return ColumnAssignment(
            alias=args[0],
            modifiers=modifiers,
            concept=self.environment.concepts[concept[0]],
        )

    def _TERMINATOR(self, args):
        return None

    def MODIFIER(self, args) -> Modifier:
        return Modifier(args.value)

    def PURPOSE(self, args) -> Purpose:
        return Purpose(args.value)

    def PROPERTY(self, args):
        return Purpose.PROPERTY

    @v_args(meta=True)
    def concept_property_declaration(self, meta: Meta, args) -> Concept:

        if len(args) > 3:
            metadata = args[3]
        else:
            metadata = None
        grain, name = args[1].rsplit(".", 1)
        self.validate_concept(name, meta)
        concept = Concept(
            name=name,
            datatype=args[2],
            purpose=args[0],
            metadata=metadata,
            grain=Grain(components=[self.environment.concepts[grain]]),
            namespace=self.environment.namespace,
            keys=[self.environment.concepts[grain]],
        )
        self.environment.concepts[name] = concept
        return concept

    @v_args(meta=True)
    def concept_declaration(self, meta: Meta, args) -> Concept:

        if len(args) > 3:
            metadata = args[3]
        else:
            metadata = None
        name = args[1]
        lookup, namespace, name = parse_concept_reference(name, self.environment)
        self.validate_concept(lookup, meta)
        concept = Concept(
            name=name,
            datatype=args[2],
            purpose=args[0],
            metadata=metadata,
            namespace=namespace,
        )
        if concept.metadata:
            concept.metadata.line_number = meta.line
        self.environment.concepts[lookup] = concept
        return concept

    @v_args(meta=True)
    def concept_derivation(self, meta: Meta, args) -> Concept:
        if len(args) > 3:
            metadata = args[3]
        else:
            metadata = None
        purpose = args[0]
        name = args[1]

        lookup, namespace, name = parse_concept_reference(name, self.environment)
        self.validate_concept(lookup, meta)
        if isinstance(args[2], FilterItem):
            filter_item: FilterItem = args[2]
            concept = Concept(
                name=name,
                datatype=filter_item.content.datatype,
                purpose=args[0],
                metadata=metadata,
                lineage=filter_item,
                # filters are implicitly at the grain of the base item
                grain=Grain(components=[filter_item.output]),
                namespace=namespace,
            )
            if concept.metadata:
                concept.metadata.line_number = meta.line
            self.environment.concepts[lookup] = concept
            return concept
        if isinstance(args[2], WindowItem):
            window_item: WindowItem = args[2]
            if purpose == Purpose.PROPERTY:
                keys = [window_item.content]
            else:
                keys = []
            concept = Concept(
                name=name,
                datatype=window_item.content.datatype,
                purpose=args[0],
                metadata=metadata,
                lineage=window_item,
                # windows are implicitly at the grain of the group by + the original content
                grain=Grain(components=window_item.over + [window_item.content.output]),
                namespace=namespace,
                keys=keys,
            )
            if concept.metadata:
                concept.metadata.line_number = meta.line
            self.environment.concepts[lookup] = concept
            return concept
        else:
            function: Function = args[2]
            concept = Concept(
                name=name,
                datatype=function.output_datatype,
                purpose=args[0],
                metadata=metadata,
                lineage=function,
                grain=function.output_grain,
                namespace=namespace,
            )
            if concept.metadata:
                concept.metadata.line_number = meta.line
            self.environment.concepts[lookup] = concept
            return concept

    @v_args(meta=True)
    def concept(self, meta: Meta, args) -> Concept:
        concept: Concept = args[0]
        if concept.metadata:
            concept.metadata.line_number = meta.line
        return args[0]

    def column_assignment_list(self, args):
        return args

    def column_list(self, args) -> List:
        return args

    def grain_clause(self, args) -> Grain:
        #            namespace=self.environment.namespace,
        return Grain(components=[self.environment.concepts[a] for a in args[0]])

    @v_args(meta=True)
    def datasource(self, meta: Meta, args):
        name = args[0]
        columns: List[ColumnAssignment] = args[1]
        grain: Optional[Grain] = None
        address: Optional[Address] = None
        for val in args[1:]:
            if isinstance(val, Address):
                address = val
            elif isinstance(val, Grain):
                grain = val
            elif isinstance(val, Query):
                address = Address(location=f"({val.text})")
        if not address:
            raise ValueError(
                "Malformed datasource, missing address or query declaration"
            )
        datasource = Datasource(
            identifier=name,
            columns=columns,
            # grain will be set by default from args
            # TODO: move to factory
            grain=grain,  # type: ignore
            address=address,
            namespace=self.environment.namespace,
        )
        for column in columns:
            column.concept = column.concept.with_grain(datasource.grain)
        self.environment.datasources[datasource.identifier] = datasource
        return datasource

    @v_args(meta=True)
    def comment(self, meta: Meta, args):
        assert len(args) == 1
        return Comment(text=args[0].value)

    @v_args(meta=True)
    def select_transform(self, meta, args) -> ConceptTransform:
        function: Function = args[0]
        output: str = args[1]

        lookup, namespace, output = parse_concept_reference(output, self.environment)
        self.validate_concept(lookup, meta)
        # keys are used to pass through derivations

        if function.output_purpose == Purpose.PROPERTY:
            grain = Grain(components=function.arguments)
            keys = [x for x in function.arguments if isinstance(x, Concept)]
        else:
            grain = None
            keys = None
        concept = Concept(
            name=output,
            datatype=function.output_datatype,
            purpose=function.output_purpose,
            lineage=function,
            namespace=namespace,
            grain=grain,
            keys=keys,
        )
        if concept.metadata:
            concept.metadata.line_number = meta.line
        # We don't assign it here because we'll do this later when we know the grain
        self.environment.concepts[lookup] = concept
        return ConceptTransform(function=function, output=concept)

    @v_args(meta=True)
    def select_item(self, meta: Meta, args) -> Optional[SelectItem]:

        args = [arg for arg in args if not isinstance(arg, Comment)]
        if not args:
            return None
        if len(args) != 1:
            raise ParseError(
                f"Malformed select statement {args} {self.text[meta.start_pos:meta.end_pos]}"
            )
        content = args[0]
        if isinstance(content, ConceptTransform):
            return SelectItem(content=content)
        return SelectItem(
            content=self.environment.concepts.__getitem__(content, meta.line)
        )

    def select_list(self, args):
        return [arg for arg in args if arg]

    def limit(self, args):
        return Limit(count=int(args[0].value))

    def ORDERING(self, args):
        return Ordering(args)

    def order_list(self, args):
        return [OrderItem(expr=x, order=y) for x, y in zip(args[::2], args[1::2])]

    def order_by(self, args):
        return OrderBy(items=args[0])

    def over_list(self, args):
        return [self.environment.concepts[x] for x in args]

    def import_statement(self, args):
        alias = args[-1]
        path = args[0].split(".")

        target = join(self.environment.working_path, *path) + ".preql"
        with open(target, "r", encoding="utf-8") as f:

            text = f.read()
        nparser = ParseToObjects(
            visit_tokens=True,
            text=text,
            environment=Environment(working_path=dirname(target), namespace=alias),
        )
        nparser.transform(PARSER.parse(text))

        for key, concept in nparser.environment.concepts.items():
            self.environment.concepts[f"{alias}.{key}"] = concept
        for key, datasource in nparser.environment.datasources.items():
            self.environment.datasources[f"{alias}.{key}"] = datasource
        return None

    @v_args(meta=True)
    def select(self, meta: Meta, args) -> Select:

        select_items = None
        limit = None
        order_by = None
        where = None
        for arg in args:
            if isinstance(arg, List):
                select_items = arg
            elif isinstance(arg, Limit):
                limit = arg.count
            elif isinstance(arg, OrderBy):
                order_by = arg
            elif isinstance(arg, WhereClause):
                where = arg
        if not select_items:
            raise ValueError("Malformed select, missing select items")
        output = Select(
            selection=select_items, where_clause=where, limit=limit, order_by=order_by
        )
        for item in select_items:
            # we don't know the grain of an aggregate at assignment time
            # so rebuild at this point in the tree
            # TODO: simplify
            if isinstance(item.content, ConceptTransform):
                new_concept = item.content.output.with_grain(output.grain)
                self.environment.concepts[new_concept.name] = new_concept
                item.content.output = new_concept
            # elif isinstance(item.content, Concept):
            #     # new_concept = item.content.with_grain(output.grain)
            #     item.content = new_concept
            # elif isinstance(item.content, WindowItem):
            #     new_concept = item.content.output.with_grain(output.grain)
            #     item.content.output = new_concept
            # else:
            #     raise ValueError

        if order_by:
            for item in order_by.items:
                if (
                    isinstance(item.expr, Concept)
                    and item.expr.purpose == Purpose.METRIC
                ):
                    item.expr = item.expr.with_grain(output.grain)
        return output

    @v_args(meta=True)
    def address(self, meta: Meta, args):
        return Address(location=args[0])

    @v_args(meta=True)
    def query(self, meta: Meta, args):
        return Query(text=args[0][3:-3])

    def where(self, args):
        return WhereClause(conditional=args[0])

    def int_lit(self, args):
        return int(args[0])

    def bool_lit(self, args):
        return bool(args[0].capitalize())

    def float_lit(self, args):
        return float(args[0])

    def literal(self, args):
        return args[0]

    def comparison(self, args) -> Comparison:
        return Comparison(left=args[0], right=args[2], operator=args[1])

    def expr_tuple(self, args):
        return tuple(args)

    def in_comparison(self, args):
        # x in (a,b,c)
        return Comparison(left=args[0], right=args[1], operator=ComparisonOperator.IN)

    def conditional(self, args):
        return Conditional(left=args[0], right=args[2], operator=args[1])

    def window_order(self, args):
        return WindowOrder(args[0])

    def window_order_by(self, args):
        # flatten tree
        return args[0]

    def window(self, args):
        return Window(count=args[1].value, window_order=args[0])

    def WINDOW_TYPE(self, args):
        return WindowType(args.strip())

    def window_item_over(self, args):
        return WindowItemOver(contents=args[0])

    def window_item_order(self, args):
        return WindowItemOrder(contents=args[0])

    def window_item(self, args) -> WindowItem:
        type = args[0]
        order_by = []
        over = []
        for item in args[2:]:
            if isinstance(item, WindowItemOrder):
                order_by = item.contents
            elif isinstance(item, WindowItemOver):
                over = item.contents
        concept = self.environment.concepts[args[1]]
        return WindowItem(type=type, content=concept, over=over, order_by=order_by)

    def filter_item(self, args) -> FilterItem:
        where: WhereClause
        string_concept, where = args
        concept = self.environment.concepts[string_concept]
        return FilterItem(content=concept, where=where)

    # BEGIN FUNCTIONS
    @v_args(meta=True)
    def expr_reference(self, meta, args) -> Concept:
        return self.environment.concepts.__getitem__(args[0], meta.line)

    def expr(self, args):
        if len(args) > 1:
            raise ParseError("Expression should have one child only.")
        return args[0]

    def count(self, args):
        return Function(
            operator=FunctionType.COUNT,
            arguments=args,
            output_datatype=DataType.INTEGER,
            output_purpose=Purpose.METRIC,
            arg_count=1,
        )

    def count_distinct(self, args):
        return Function(
            operator=FunctionType.COUNT_DISTINCT,
            arguments=args,
            output_datatype=DataType.INTEGER,
            output_purpose=Purpose.METRIC,
            arg_count=1,
        )

    def sum(self, arguments):
        return Function(
            operator=FunctionType.SUM,
            arguments=arguments,
            output_datatype=arguments[0].datatype,
            output_purpose=Purpose.METRIC,
            arg_count=1
            # output_grain=Grain(components=arguments),
        )

    def avg(self, arguments):

        arg = arguments[0]

        return Function(
            operator=FunctionType.AVG,
            arguments=arguments,
            output_datatype=arg.datatype,
            output_purpose=Purpose.METRIC,
            valid_inputs={DataType.INTEGER, DataType.FLOAT, DataType.NUMBER},
            arg_count=1
            # output_grain=Grain(components=arguments),
        )

    def max(self, arguments):
        return Function(
            operator=FunctionType.MIN,
            arguments=arguments,
            output_datatype=arguments[0].datatype,
            output_purpose=Purpose.METRIC,
            valid_inputs={DataType.INTEGER, DataType.FLOAT, DataType.NUMBER},
            arg_count=1
            # output_grain=Grain(components=arguments),
        )

    def min(self, arguments):
        return Function(
            operator=FunctionType.MAX,
            arguments=arguments,
            output_datatype=arguments[0].datatype,
            output_purpose=Purpose.METRIC,
            valid_inputs={DataType.INTEGER, DataType.FLOAT, DataType.NUMBER},
            arg_count=1
            # output_grain=Grain(components=arguments),
        )

    def len(self, args):
        return Function(
            operator=FunctionType.LENGTH,
            arguments=args,
            output_datatype=DataType.INTEGER,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.STRING, DataType.ARRAY, DataType.MAP},
            # output_grain=args[0].grain,
        )

    def concat(self, args):
        return Function(
            operator=FunctionType.CONCAT,
            arguments=args,
            output_datatype=DataType.STRING,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.STRING},
            arg_count=99
            # output_grain=args[0].grain,
        )

    def like(self, args):
        return Function(
            operator=FunctionType.LIKE,
            arguments=args,
            output_datatype=DataType.BOOL,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.STRING},
            arg_count=2
            # output_grain=Grain(components=args),
        )

    def ilike(self, args):
        return Function(
            operator=FunctionType.ILIKE,
            arguments=args,
            output_datatype=DataType.BOOL,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.STRING},
            arg_count=2
            # output_grain=Grain(components=args),
        )

    def upper(self, args):
        return Function(
            operator=FunctionType.UPPER,
            arguments=args,
            output_datatype=DataType.STRING,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.STRING},
            arg_count=1
            # output_grain=Grain(components=args),
        )

    def lower(self, args):
        return Function(
            operator=FunctionType.LOWER,
            arguments=args,
            output_datatype=DataType.STRING,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.STRING},
            arg_count=1
            # output_grain=Grain(components=args),
        )

    # date functions
    def fdate(self, args):
        return Function(
            operator=FunctionType.DATE,
            arguments=args,
            output_datatype=DataType.DATE,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={
                DataType.DATE,
                DataType.TIMESTAMP,
                DataType.DATETIME,
                DataType.STRING,
            },
            arg_count=1,
        )

    def fdatetime(self, args):
        return Function(
            operator=FunctionType.DATETIME,
            arguments=args,
            output_datatype=DataType.DATETIME,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={
                DataType.DATE,
                DataType.TIMESTAMP,
                DataType.DATETIME,
                DataType.STRING,
            },
            arg_count=1,
        )

    def ftimestamp(self, args):
        return Function(
            operator=FunctionType.TIMESTAMP,
            arguments=args,
            output_datatype=DataType.TIMESTAMP,
            output_purpose=Purpose.PROPERTY,
            valid_inputs=[{DataType.TIMESTAMP, DataType.STRING}],
            arg_count=1,
        )

    def fsecond(self, args):
        return Function(
            operator=FunctionType.SECOND,
            arguments=args,
            output_datatype=DataType.INTEGER,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=1,
        )

    def fminute(self, args):
        return Function(
            operator=FunctionType.MINUTE,
            arguments=args,
            output_datatype=DataType.INTEGER,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=1,
        )

    def fhour(self, args):
        return Function(
            operator=FunctionType.HOUR,
            arguments=args,
            output_datatype=DataType.INTEGER,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=1,
        )

    def fday(self, args):
        return Function(
            operator=FunctionType.DAY,
            arguments=args,
            output_datatype=DataType.INTEGER,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.DATE, DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=1,
        )

    def fweek(self, args):
        return Function(
            operator=FunctionType.WEEK,
            arguments=args,
            output_datatype=DataType.INTEGER,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.DATE, DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=1,
        )

    def fmonth(self, args):
        return Function(
            operator=FunctionType.MONTH,
            arguments=args,
            output_datatype=DataType.INTEGER,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.DATE, DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=1,
        )

    def fquarter(self, args):
        return Function(
            operator=FunctionType.QUARTER,
            arguments=args,
            output_datatype=DataType.INTEGER,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.DATE, DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=1,
        )

    def fyear(self, args):
        return Function(
            operator=FunctionType.YEAR,
            arguments=args,
            output_datatype=DataType.INTEGER,
            output_purpose=Purpose.PROPERTY,
            valid_inputs={DataType.DATE, DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=1,
        )

    # utility functions
    def fcast(self, args) -> Function:
        input_concept: Concept = args[0]
        output_datatype = args[1]
        return Function(
            operator=FunctionType.CAST,
            arguments=args,
            output_datatype=output_datatype,
            output_purpose=input_concept.purpose,
            valid_inputs={
                DataType.INTEGER,
                DataType.STRING,
                DataType.FLOAT,
                DataType.NUMBER,
            },
            arg_count=2,
        )

    # math functions
    def fadd(self, args):
        output_datatype = arg_to_datatype(args[0])
        # TODO: check for valid transforms?
        return Function(
            operator=FunctionType.ADD,
            arguments=args,
            output_datatype=output_datatype,
            output_purpose=Purpose.PROPERTY,
            # valid_inputs={DataType.DATE, DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=2,
        )

    def fsub(self, args):
        output_datatype = arg_to_datatype(args[0])
        return Function(
            operator=FunctionType.SUBTRACT,
            arguments=args,
            output_datatype=output_datatype,
            output_purpose=Purpose.PROPERTY,
            # valid_inputs={DataType.DATE, DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=2,
        )

    def fmul(self, args):
        output_datatype = arg_to_datatype(args[0])
        return Function(
            operator=FunctionType.MULTIPLY,
            arguments=args,
            output_datatype=output_datatype,
            output_purpose=Purpose.PROPERTY,
            # valid_inputs={DataType.DATE, DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=2,
        )

    def fdiv(self, args):
        output_datatype = arg_to_datatype(args[0])
        return Function(
            operator=FunctionType.DIVIDE,
            arguments=args,
            output_datatype=output_datatype,
            output_purpose=Purpose.PROPERTY,
            # valid_inputs={DataType.DATE, DataType.TIMESTAMP, DataType.DATETIME},
            arg_count=2,
        )

    def fround(self, args):
        output_datatype = arg_to_datatype(args[0])
        return Function(
            operator=FunctionType.ROUND,
            arguments=args,
            output_datatype=output_datatype,
            output_purpose=Purpose.PROPERTY,
            valid_inputs=[
                {DataType.INTEGER, DataType.FLOAT, DataType.NUMBER},
                {DataType.INTEGER},
            ],
            arg_count=2,
        )

    # def fcase(self, args):
    #     output_datatype = arg_to_datatype(args[0])
    #     return Function(
    #         operator=FunctionType.CASE,
    #         arguments=args,
    #         output_datatype=output_datatype,
    #         output_purpose=Purpose.PROPERTY,
    #         # valid_inputs=[{DataType.INTEGER, DataType.FLOAT, DataType.NUMBER}, {DataType.INTEGER}],
    #         # arg_count=2,
    #     )


def unpack_visit_error(e: VisitError):
    """This is required to get exceptions from imports, which would
    raise nested VisitErrors"""
    if isinstance(e.orig_exc, VisitError):
        unpack_visit_error(e.orig_exc)
    if isinstance(e.orig_exc, (UndefinedConceptException, TypeError)):
        raise e.orig_exc
    elif isinstance(e.orig_exc, ValidationError):
        raise InvalidSyntaxException(str(e.orig_exc))
    raise e


def parse_text(
    text: str, environment: Optional[Environment] = None, print_flag: bool = False
) -> Tuple[Environment, List]:
    environment = environment or Environment(datasources={})
    parser = ParseToObjects(visit_tokens=True, text=text, environment=environment)
    try:
        output = [v for v in parser.transform(PARSER.parse(text)) if v]
    except VisitError as e:
        unpack_visit_error(e)
        # this will never be reached
        raise e
    except (
        UnexpectedCharacters,
        UnexpectedEOF,
        UnexpectedInput,
        UnexpectedToken,
        ValidationError,
    ) as e:
        raise InvalidSyntaxException(str(e))

    return environment, output
