from typing import Any
import yaml


class IndentedListDumper(yaml.Dumper):
    def increase_indent(self, flow=False, indentless=False):
        """Always indent list items, preventing PyYAML's default indentless block sequences."""
        return super().increase_indent(flow, False)


def dump(data: dict[Any, Any]) -> str:
    """Serialize a manifest dict to a YAML string with indented list items."""
    return yaml.dump(data, Dumper=IndentedListDumper, default_flow_style=False)
