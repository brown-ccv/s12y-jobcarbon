import yaml


class FixedDumper(yaml.Dumper):
    def increase_indent(self, flow=False, indentless=False):
        return super(FixedDumper, self).increase_indent(flow, False)


def dump(data):
    return yaml.dump(data, Dumper=FixedDumper, default_flow_style=False)
