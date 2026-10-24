"""Explicit record fields for small local models; no changes to cloud schemas."""

from copy import deepcopy


def explicit_schema(model):
    schema = deepcopy(model.model_json_schema())

    def visit(node):
        if isinstance(node, dict):
            node.pop('default', None)
            if 'properties' in node:
                node['required'] = list(node['properties'])
                node['additionalProperties'] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema
