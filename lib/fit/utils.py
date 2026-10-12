"""Small helpers for reading and writing fit_tool messages."""

from fit_tool.field_definition import FieldDefinition

FIT_EPOCH_OFFSET = 631065600  # FIT timestamps count from 1989-12-31 00:00 UTC
SEMICIRCLES_TO_DEG = 180.0 / 2**31


def secs(ms):
    """fit_tool timestamps are in milliseconds; FIT data is per second."""
    return ms // 1000


def set_field(msg, name, value):
    """Sets a field, allowing it to be added even if the original message did not contain it."""
    field = msg.get_field_by_name(name)
    field.growable = True
    field.set_value(0, value)


def set_if_defined(msg, name, value):
    """Sets a field only if the watch wrote it (present in the message definition)."""
    field = msg.get_field_by_name(name)
    if field and msg.definition_message.get_field_definition(field.field_id) is not None:
        field.growable = True
        field.set_value(0, value)


def valid_value(msg, name):
    """Field value, or None when missing or holding the FIT 'invalid' marker (e.g. 255 for no HR)."""
    field = msg.get_field_by_name(name)
    if not field or not field.encoded_values:
        return None
    raw = field.encoded_values[0]
    if raw is None or raw == field.base_type.invalid_raw_value():
        return None
    return field.get_value(0)


def add_field_definitions(definition, msg_class, names):
    """Adds definitions for the named fields of msg_class that the definition does not contain yet."""
    proto = msg_class()
    for name in names:
        field = proto.get_field_by_name(name)
        if definition.get_field_definition(field.field_id) is None:
            definition.add_field_definition(FieldDefinition(field.field_id, field.base_type.size, field.base_type))
