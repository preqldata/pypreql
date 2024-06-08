from preql.core.models import Environment
from preql.core.models import (
    Concept,
    CTE,
)
from preql.core.processing.nodes.base_node import StrategyNode
from preql.core.processing.nodes import GroupNode, SelectNode, MergeNode, WindowNode
from preql.core.processing.concept_strategies_v3 import source_query_concepts
from preql.core.enums import Purpose
from preql.core.env_processor import generate_graph


def fingerprint(node: StrategyNode) -> str:
    base = node.__class__.__name__ + ",".join(
        [fingerprint(node) for node in node.parents]
    )
    if isinstance(node, SelectNode):
        base += node.datasource.name
    base += str(node.conditions)
    base += str(node.force_group)
    return base


def dedupe_nodes(nodes):
    seen = set()
    output = []
    for node in nodes:
        if fingerprint(node) not in seen:
            output.append(node)
            seen.add(fingerprint(node))
    return output


def _get_parents(node: StrategyNode):
    output = [node]
    for parent in node.parents:
        output = _get_parents(parent) + output
    return dedupe_nodes(output)


def get_parents(node: StrategyNode):
    return [node.__class__ for node in _get_parents(node)]


def validate_shape(input: list[Concept], environment: Environment, g, levels):
    """test that our query resolves to the expected CTES"""
    base: GroupNode = source_query_concepts(input, environment, g)
    final = get_parents(base)
    assert final == levels


def test_demo_filter(normalized_engine, test_env):
    executor = normalized_engine
    env = test_env
    assert "passenger.id.count" not in env.materialized_concepts
    executor.environment = env

    test = """
    auto surviving_passenger<- filter passenger.id where passenger.survived =1; 
select 
    passenger.last_name,
    passenger.id.count,
    count(surviving_passenger) -> surviving_size
order by
    passenger.id.count desc
limit 5;"""

    executor.parse_text(test)[-1]

    # ensure that last name is only fetched from the dimension
    last_name = env.concepts["passenger.last_name"]

    def recurse_cte(cte: CTE):
        if last_name in cte.output_columns:
            assert last_name.address in cte.source_map
        for parent in cte.parent_ctes:
            recurse_cte(parent)


def test_rowset_shape(normalized_engine, test_env):
    executor = normalized_engine
    env = test_env
    assert "passenger.id.count" not in env.materialized_concepts
    executor.environment = env

    test = """
    rowset survivors<- select 
    passenger.last_name, 
    passenger.name,
    passenger.id, 
    passenger.survived,
    passenger.age,  
where 
    passenger.survived =1; 

# now we can reference our rowset like any other concept
select 
    --survivors.passenger.id,
    survivors.passenger.name,
    survivors.passenger.last_name,
    survivors.passenger.age,
    --row_number survivors.passenger.id over survivors.passenger.name order by survivors.passenger.age desc -> eldest
where 
    eldest = 1
order by survivors.passenger.name desc
limit 5;"""

    sql = executor.parse_text(test)[-1]

    assert env.concepts["local.eldest"].purpose == Purpose.PROPERTY
    # for x in env.concepts["local.eldest"].keys:
    #     if x.address != 'survivors.passenger.id' and x.address != 'passenger.id':
    #         assert x.keys, f"address fir {x.address}  {x.purpose} should have keys"
    # assert {x.address for x in env.concepts["local.eldest"].keys} == {env.concepts["survivors.passenger.id"].address}

    # actual = executor.generate_sql(sql)
    # logger.info(actual)
    g = generate_graph(env)
    validate_shape(
        sql.output_columns,
        env,
        g,
        levels=[
            SelectNode,  # select store
            SelectNode,  # select year
            MergeNode,
            WindowNode,  # calculate aggregate
            MergeNode,  # enrich store name
            MergeNode,  # final node
            MergeNode,  # final node
            GroupNode,  # final node
        ],
    )
