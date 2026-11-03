from .. import config
from .base import Engine, EngineError
from .meshy import MeshyEngine
from .local_command import LocalCommandEngine
from .fixture import FixtureEngine


def registry() -> dict[str, Engine]:
    r: dict[str, Engine] = {"local_cmd": LocalCommandEngine(), "meshy": MeshyEngine()}
    if config.ENABLE_FIXTURE:
        r["fixture"] = FixtureEngine()
    return r
