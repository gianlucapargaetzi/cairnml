import click
from clearml import Task


@click.group()
@click.option(
    '--print-something/--dont-print-something',
    default=True,
)
@click.option(
    '--what-to-print',
    default='something',
)
def cli(print_something, what_to_print):
    Task.init(
        project_name='examples',
        task_name='Click multi command',
    )
    if print_something:
        print(what_to_print)


@cli.command(
    'hello',
    help='test help',
)
@click.option(
    '--count',
    default=1,
    help='Number of greetings.',
)
@click.option(
    '--name',
    prompt='Your name',
    help='The person to greet.',
)
def hello(count, name):
    """
    Simple program that greets NAME for a total of COUNT times.
    """
    for _ in range(count):
        click.echo(f"Hello {name}!")


CONTEXT_SETTINGS = {
    "default_map": {
        'runserver': {
            'port': 5000,
        },
    }
}


@cli.command('runserver')
@click.option('--port', default=8000)
@click.option('--name', help='service name')
def runserver(port, name):
    click.echo(f"Serving on http://127.0.0.1:{port} {name}/")


if __name__ == '__main__':
    cli()
