# from preql.compiler import compile
from os.path import dirname, join

import pytest
from preql import Executor
from preql.core.env_processor import generate_graph
from preql.core.models import (
    Select,
    QueryDatasource,
    Grain,
    Environment,
    ProcessedQuery,
    ProcessedQueryPersist,
    Concept,
)

from preql.core.processing.concept_strategies_v3 import search_concepts
from preql.core.query_processor import datasource_to_ctes, get_query_datasources
from preql.dialect.sql_server import SqlServerDialect
from preql.parser import parse
from preql.core.processing.nodes import GroupNode, MergeNode, SelectNode

@pytest.mark.adventureworks
def test_parsing(environment: Environment):
    with open(
        join(dirname(__file__), "finance_queries.preql"), "r", encoding="utf-8"
    ) as f:
        file = f.read()
    SqlServerDialect()
    environment, statements = parse(file, environment=environment)


@pytest.mark.adventureworks_execution
def test_finance_queries(adventureworks_engine: Executor, environment: Environment):
    with open(
        join(dirname(__file__), "finance_queries.preql"), "r", encoding="utf-8"
    ) as f:
        file = f.read()
    generator = SqlServerDialect()
    environment, statements = parse(file, environment=environment)
    sql = generator.generate_queries(environment, statements)

    for statement in sql:
        if not isinstance(statement, (ProcessedQuery, ProcessedQueryPersist)):
            continue
        generator.compile_statement(statement)
        results = adventureworks_engine.execute_query(statement)
        assert list(results)[0] == ("Canadian Division", 8, 292174782.71999985)


@pytest.mark.adventureworks
def test_query_datasources(environment: Environment):
    with open(
        join(dirname(__file__), "online_sales_queries.preql"), "r", encoding="utf-8"
    ) as f:
        file = f.read()
    environment, statements = parse(file, environment=environment)
    assert (
        str(environment.datasources["internet_sales.fact_internet_sales"].grain)
        == "Grain<internet_sales.order_line_number,internet_sales.order_number>"
    )

    test: Select = statements[-1]  # multipart join

    environment_graph = generate_graph(environment)
    from preql.hooks.query_debugger import print_recursive_nodes

    # assert a group up to the first name works

    # source query concepts includes extra group by to grain
    customer_node = search_concepts(
        [environment.concepts["customer.first_name"]],
        environment=environment,
        g=environment_graph,
        depth=0,
    )
    print_recursive_nodes(customer_node)
    customer_datasource = customer_node.resolve()

    assert customer_datasource.identifier == "customers_at_customer_customer_id"

    # assert a join before the group by works
    t_grain = Grain(
        components=[
            environment.concepts["internet_sales.order_number"],
            environment.concepts["customer.first_name"],
        ]
    )
    customer_datasource = search_concepts(
        [environment.concepts["internet_sales.order_number"]] + t_grain.components_copy,
        environment=environment,
        g=environment_graph,
        depth=0,
    ).resolve()

    # assert a group up to the first name works
    customer_datasource = search_concepts(
        [environment.concepts["customer.first_name"]],
        environment=environment,
        g=environment_graph,
        depth=0,
    ).resolve()

    assert customer_datasource.identifier == "customers_at_customer_customer_id"

    datasource = get_query_datasources(
        environment=environment, graph=environment_graph, statement=test
    )


    cte = datasource_to_ctes(datasource)[0]

    assert {c.address for c in cte.output_columns} == {
        "customer.first_name",
        "internet_sales.order_line_number",
        "internet_sales.order_number",
        "internet_sales.total_sales_amount",
    }
    assert len(cte.output_columns) == 4


def recurse_datasource(parent: QueryDatasource, depth=0):
    for x in parent.datasources:
        if isinstance(x, QueryDatasource):
            recurse_datasource(x, depth + 1)


def list_to_address(clist: list[Concept])->set[str]:
    return set([c.address for c in clist])


@pytest.mark.adventureworks
def test_two_properties(environment: Environment):
    with open(
        join(dirname(__file__), "online_sales_queries.preql"), "r", encoding="utf-8"
    ) as f:
        file = f.read()
    environment, statements = parse(file, environment=environment)
    test: Select = statements[-3]

    environment_graph = generate_graph(environment)

    # assert a group up to the first name works
    customer_datasource = search_concepts(
        [environment.concepts["customer.first_name"]] + test.grain.components_copy,
        environment=environment,
        g=environment_graph,
        depth=0,
    ).resolve()

    assert list_to_address(customer_datasource.output_concepts).issuperset(list_to_address(
        [environment.concepts["customer.first_name"]] + test.grain.components_copy
    ))

    order_date_datasource = search_concepts(
        [environment.concepts["dates.order_date"]] + test.grain.components_copy,
        environment=environment,
        g=environment_graph,
        depth=0,
    ).resolve()

    assert (
        list_to_address(order_date_datasource.output_concepts).issuperset(list_to_address([environment.concepts["dates.order_date"]] + test.grain.components_copy))
    )

    get_query_datasources(
        environment=environment, graph=environment_graph, statement=test
    )



@pytest.mark.adventureworks
def test_grain(environment: Environment):
    from preql.core.processing.node_generators import gen_group_to_node
    from preql.core.processing.concept_strategies_v3 import search_concepts
    with open(
        join(dirname(__file__), "online_sales_queries.preql"), "r", encoding="utf-8"
    ) as f:
        file = f.read()
    environment, statements = parse(file, environment=environment)
    environment_graph = generate_graph(environment)
    test = search_concepts([environment.concepts['dates.order_date'],
                          environment.concepts['dates.order_key']],
                          environment=environment, depth =0,
                          g= environment_graph,
                        )
    assert isinstance(test, SelectNode)
    assert len(test.parents) == 0
    assert test.grain.set == Grain(components =[environment.concepts['dates.order_key']]).set
    assert environment.datasources['dates.order_dates'].grain.set  == Grain(components = [environment.concepts['dates.order_key']]).set
    resolved = test.resolve()
    assert resolved.grain == Grain(components =[environment.concepts['dates.order_key']])
    assert test.grain == resolved.grain
    assert resolved.group_required == False

@pytest.mark.adventureworks
def test_group_to_grain(environment: Environment):
    from preql.core.processing.node_generators import gen_group_to_node
    from preql.core.processing.concept_strategies_v3 import search_concepts
    with open(
        join(dirname(__file__), "online_sales_queries.preql"), "r", encoding="utf-8"
    ) as f:
        file = f.read()
    environment, statements = parse(file, environment=environment)
    environment_graph = generate_graph(environment)
    assert len(environment.concepts['internet_sales.total_sales_amount_debug'].grain.components) == 2
    test = search_concepts([environment.concepts['internet_sales.total_sales_amount_debug'],
                          environment.concepts['dates.order_date']],
                          environment=environment, depth =0,
                          g= environment_graph,
                        )
    assert isinstance(test, MergeNode)
    assert test.whole_grain == True
    assert len(test.parents) == 2
    resolved = test.resolve()
    group_parent = [x for x in test.parents if isinstance(x, GroupNode)][0]
    merge_node = [x for x in test.parents if isinstance(x, MergeNode)][0]

    assert resolved.grain == Grain(concepts = [environment.concepts['internet_sales.order_number'],
                                           environment.concepts['internet_sales.order_line_number']])
    assert resolved.force_group is False
    assert resolved.group_required is False


@pytest.mark.adventureworks
def test_two_properties_query(environment: Environment):
    from preql.core.processing.node_generators import gen_group_node
    from preql.core.processing.concept_strategies_v3 import search_concepts
    with open(
        join(dirname(__file__), "online_sales_queries.preql"), "r", encoding="utf-8"
    ) as f:
        file = f.read()
    environment, statements = parse(file, environment=environment)
    environment_graph = generate_graph(environment)
    assert len(environment.concepts['internet_sales.total_sales_amount_debug'].grain.components) == 2
    test = gen_group_node(environment.concepts['total_sales_amount_debug_2'],
                          local_optional = [environment.concepts['dates.order_date']],
                          environment=environment, depth =0,
                          g= environment_graph,
                          source_concepts = search_concepts)

    test: Select = statements[-3]
    generator = SqlServerDialect()
    sql2 = generator.generate_queries(environment, [test])
    compiled = generator.compile_statement(sql2[0])

    


@pytest.mark.adventureworks_execution
def test_online_sales_queries(
    adventureworks_engine: Executor, environment: Environment
):
    with open(
        join(dirname(__file__), "online_sales_queries.preql"), "r", encoding="utf-8"
    ) as f:
        file = f.read()
    generator = SqlServerDialect()
    environment, statements = parse(file, environment=environment)
    sql = generator.generate_queries(environment, statements)

    for statement in sql:
        generator.compile_statement(statement)
        adventureworks_engine.execute_query(statement).fetchall()
