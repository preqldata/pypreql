from click import Path, argument, option, group, pass_context, UNPROCESSED
from preql import Executor, Environment, parse
from preql.dialect.enums import Dialects
from datetime import datetime
from pathlib import Path as PathlibPath
from preql.hooks.query_debugger import DebuggingHook
from preql.parsing.render import Renderer


def print_tabulate(q, tabulate):
    result = q.fetchall()
    print(tabulate(result, headers=q.keys(), tablefmt="psql"))


def pairwise(t):
    it = iter(t)
    return zip(it, it)


def extra_to_kwargs(arg_list: list[str]) -> dict[str, str]:
    pairs = pairwise(arg_list)
    final = {}
    for k, v in pairs:
        k = k.lstrip("--")
        final[k] = v
    return final


@group()
@option("--debug", default=False)
@pass_context
def cli(ctx, debug: bool):
    ctx.ensure_object(dict)
    ctx.obj["DEBUG"] = debug


@cli.command("fmt")
@argument("input", type=Path(exists=True))
@pass_context
def fmt(ctx, input):
    start = datetime.now()
    with open(input, "r") as f:
        script = f.read()

    _, queries = parse(script)
    r = Renderer()
    with open(input, "w") as f:
        f.write("\n".join([r.to_string(x) for x in queries]))
    print(f"Completed all in {(datetime.now()-start)}")


@cli.command(
    "run",
    context_settings=dict(
        ignore_unknown_options=True,
    ),
)
@argument("input", type=Path())
@argument("dialect", type=str)
@argument("conn_args", nargs=-1, type=UNPROCESSED)
@pass_context
def run(ctx, input, dialect: str, conn_args):
    if PathlibPath(input).exists():
        inputp = PathlibPath(input)
        with open(input, "r") as f:
            script = f.read()
        namespace = inputp.stem
        directory = inputp.parent
    else:
        script = input
        namespace = None
        directory = PathlibPath.cwd()
    edialect = Dialects(dialect)

    debug = ctx.obj["DEBUG"]
    conn_dict = extra_to_kwargs((conn_args))
    if edialect == Dialects.DUCK_DB:
        from preql.dialect.config import DuckDBConfig

        conf = DuckDBConfig(**conn_dict)
    elif edialect == Dialects.SNOWFLAKE:
        from preql.dialect.config import SnowflakeConfig

        conf = SnowflakeConfig(**conn_dict)
    elif edialect == Dialects.SQL_SERVER:
        from preql.dialect.config import SQLServerConfig

        conf = SQLServerConfig(**conn_dict)
    elif edialect == Dialects.POSTGRES:
        from preql.dialect.config import PostgresConfig

        conf = PostgresConfig(**conn_dict)
    else:
        conf = None
    exec = Executor(
        dialect=edialect,
        engine=edialect.default_engine(conf=conf),
        environment=Environment(working_path=str(directory), namespace=namespace),
        hooks=[DebuggingHook()] if debug else [],
    )

    queries = exec.parse_text(script)
    start = datetime.now()
    print(f"Executing {len(queries)} statements...")
    for idx, query in enumerate(queries):
        lstart = datetime.now()
        results = exec.execute_statement(query)
        end = datetime.now()
        print(f"Statement {idx+1} of {len(queries)} done, duration: {end-lstart}.")
        if not results:
            continue
        try:
            import tabulate

            print_tabulate(results, tabulate.tabulate)
        except ImportError:
            print(", ".join(results.keys()))
            for row in results:
                print(row)
            print("---")
    print(f"Completed all in {(datetime.now()-start)}")


if __name__ == "__main__":
    cli()
