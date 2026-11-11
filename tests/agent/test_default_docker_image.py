import re
from pathlib import Path

import cairnml_agent

DEFAULT_CONF = Path(cairnml_agent.__file__).parent / "backend_api" / "config" / "default" / "agent.conf"


def test_the_default_job_image_has_a_python_the_agent_supports():
    """
    A job container installs the agent into the image's own python. Ubuntu 20.04 ships Python 3.8, which the agent
    (python >= 3.9) refuses: "requires a different Python: 3.8.10 not in '>=3.9'".
    """
    text = DEFAULT_CONF.read_text()
    block = text[text.index("default_docker:"):]
    image = re.search(r'image:\s*"([^"]+)"', block).group(1)
    ubuntu = re.search(r"ubuntu(\d+)\.(\d+)$", image)
    assert ubuntu, image
    assert (int(ubuntu.group(1)), int(ubuntu.group(2))) >= (22, 4), image
