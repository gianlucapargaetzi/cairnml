# ClearML - example code, absl parameter logging
#
import sys

from absl import app
from absl import flags
from absl import logging

from clearml import Task

FLAGS = flags.FLAGS

flags.DEFINE_string("echo", None, "Text to echo.")
flags.DEFINE_string("another_str", "My string", "A string", module_name="test")

# Connecting ClearML with the current process,
# from here on everything is logged automatically
task = Task.init(project_name="examples", task_name="Abseil example")

flags.DEFINE_integer("echo3", 3, "Text to echo.")
flags.DEFINE_string("echo5", "5", "Text to echo.", module_name="test")


def main(_):
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    print(f"Running under Python {python_version}", file=sys.stderr)
    logging.info("echo is %s.", FLAGS.echo)
    logging.info("echo3 is %s.", FLAGS.echo3)


if __name__ == "__main__":
    app.run(main)
