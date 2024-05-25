## PreQL/Trilogy

pypreql is an experimental implementation of the [PreQL/Trilogy](https://github.com/preqldata) (prequel trilogy) language, a variant on SQL intended to embrace the best features of SQL while fixing common pain points.

Preql looks like SQL, but doesn't require table references, group by, or joins. When you query, it puts the focus on what you want to get, not how you want to get it - you've already done that work once in your data model.

It's perfect for a modern data company that just can't quit SQL, but wants less pain.

Provides a rich extension ecosystem to integrate with other tools like DBT.

The Preql language spec itself will be linked from the above repo. 

Pypreql can be run locally to parse and execute preql [.preql] models.  

You can try out an interactive demo [here](https://preqldata.dev/demo).


Preql looks like this:
```sql
SELECT
    name,
    name_count.sum
WHERE 
    name like '%elvis%'
ORDER BY
    name_count.sum desc
LIMIT 10;
```

## More Examples

Examples can be found in the [public model repository](https://github.com/preqldata/trilogy-public-models).
This is a good place to start for more complex examples.

## Backends

The current PreQLimplementation supports compiling to 3 backend SQL flavors:

- Bigquery
- SQL Server
- DuckDB


## Basic Example - Python

Preql can be run directly in python.

A bigquery example, similar to bigquery [the quickstart](https://cloud.google.com/bigquery/docs/quickstarts/query-public-dataset-console)

```python


from preql import Dialects, Environment

environment = Environment()

environment.parse('''

key name string;
key gender string;
key state string;
key year int;
key name_count int;
auto name_count.sum <- sum(name_count);

datasource usa_names(
    name:name,
    number:name_count,
    year:year,
    gender:gender,
    state:state
)
address bigquery-public-data.usa_names.usa_1910_2013;

'''
)
executor = Dialects.BIGQUERY.default_executor(environment=environment)

results = executor.execute_text(
'''SELECT
    name,
    name_count.sum
ORDER BY
    name_count.sum desc
LIMIT 10;
'''

)
# multiple queries can result from one text batch
for row in results:
    # get results for first query
    answers = row.fetchall()
    for x in answers:
        print(x)
```


## Basic Example - CLI

Preql can be run through a CLI tool, 'trilogy'.

After installing preql, you can run the trilogy CLI with two required positional arguments; the first the path to a file or a direct command,
and second the dialect to run.

`trilogy run <cmd or path to preql file> <dialect>`

> [!TIP]
> This will only work for basic backends, such as Bigquery with local default credentials; if the backend requires more configuration, the CLI will require additional config arguments.


## Developing

Clone repository and install requirements.txt and requirements-test.txt.

## Contributing

Please open an issue first to discuss what you would like to change, and then create a PR against that issue.


## Similar in space

"Better SQL" has been a popular space. We believe Trilogy/PreQL takes a different approach then the following,
but all are worth checking out:


- [malloy](https://github.com/malloydata/malloy)
- [preql](https://github.com/erezsh/Preql)
- [PREQL](https://github.com/PRQL/prql)