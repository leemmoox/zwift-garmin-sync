"""Small helpers for reading and writing fit_tool messages."""

from fit_tool.definition_message import DefinitionMessage
from fit_tool.field_definition import FieldDefinition
from fit_tool.record import Record, RecordHeader

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


def insert_messages(records, pos, messages):
    """Inserts (local_id, data message) pairs into a FitFile record list at pos.

    Each message is preceded by its definition whenever that changes, and afterwards the
    definitions that were active at pos are restored, so messages after the block still
    decode with the layout they were written with.
    """
    if not messages:
        return

    active = {}
    for r in records[:pos]:
        if isinstance(r.message, DefinitionMessage):
            active[r.header.local_id] = r.message

    block = []
    emitted = {}
    for local_id, msg in messages:
        definition = msg.definition_message
        if emitted.get(local_id) is not definition:
            block.append(Record(RecordHeader(is_definition=True, local_id=local_id), definition, dirty=True))
            emitted[local_id] = definition
        block.append(Record(RecordHeader(is_definition=False, local_id=local_id), msg, dirty=True))

    for local_id in emitted:
        if local_id in active:
            block.append(Record(RecordHeader(is_definition=True, local_id=local_id), active[local_id], dirty=True))

    records[pos:pos] = block


def add_field_definitions(definition, msg_class, names):
    """Adds definitions for the named fields of msg_class that the definition does not contain yet."""
    proto = msg_class()
    for name in names:
        field = proto.get_field_by_name(name)
        if definition.get_field_definition(field.field_id) is None:
            definition.add_field_definition(FieldDefinition(field.field_id, field.base_type.size, field.base_type))
