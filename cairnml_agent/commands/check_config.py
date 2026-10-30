from cairnml_agent.commands.base import ServiceCommandSection


class Config(ServiceCommandSection):

    def __init__(self, *args, **kwargs):
        super(Config, self).__init__(*args, only_load_config=True, **kwargs)

    def config(self, **_):
        return self._session.print_configuration()

    def init(self, server=None, name=None, force=False, file=None, device=False, **_):
        from .config import main
        return main(server=server, name=name, force=force, file=file, device=device)

    @property
    def service(self):
        """ The name of the REST service used by this command """
        return 'config'
