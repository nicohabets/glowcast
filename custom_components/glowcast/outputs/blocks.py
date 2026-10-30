"""The output block in the entry's options, alone or combined (architecture 5).

An entry follows the TV with one output block, `{"type": ..., ...}`, or with
a combined one (#11) that holds a block per output type:
`{"type": "combined", "parts": [{"type": ...}, {"type": ...}]}`. These
helpers let a type find and replace its own block either way. A type reads
only its own keys; it may hand another type's block to that type's own code
(for example a later type's settings steps pass an earlier type's block,
found by its type, to that type's own lookup). No type knowledge here.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from ..const import CONF_OUTPUT, CONF_TYPE

OUTPUT_COMBINED = "combined"
CONF_PARTS = "parts"
# In the options a part of a combined output gets (`part_options`): the
# whole combination, next to the part's own block in `options["output"]`.
CONF_COMBINATION = "combination"


def output_blocks(output: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The blocks of an output block: its parts when combined, else itself."""
    if output.get(CONF_TYPE) == OUTPUT_COMBINED:
        return list(output.get(CONF_PARTS, []))
    return [output]


def output_types(options: Mapping[str, Any]) -> list[str]:
    """The output types the entry uses, in the order of its blocks."""
    return [block[CONF_TYPE] for block in output_blocks(options[CONF_OUTPUT])]


def output_block(options: Mapping[str, Any], type_id: str) -> Mapping[str, Any] | None:
    """The entry's block of this output type, alone or in a combination."""
    return next(
        (
            block
            for block in output_blocks(options[CONF_OUTPUT])
            if block.get(CONF_TYPE) == type_id
        ),
        None,
    )


def other_block(options: Mapping[str, Any], type_id: str) -> Mapping[str, Any] | None:
    """Another type's block in the same combination, or None.

    For the options a part gets as for the entry's own: a part hands it to
    that type's own code only.
    """
    return output_block(
        {CONF_OUTPUT: options.get(CONF_COMBINATION, options[CONF_OUTPUT])}, type_id
    )


def part_options(
    options: Mapping[str, Any], block: Mapping[str, Any]
) -> dict[str, Any]:
    """The options as one part of a combined output sees them: its own block,
    and the combination it is part of."""
    return {**options, CONF_OUTPUT: block, CONF_COMBINATION: options[CONF_OUTPUT]}


def with_output_block(
    options: Mapping[str, Any], block: Mapping[str, Any]
) -> dict[str, Any]:
    """The options with this block in place of the stored one of its type."""
    output = options[CONF_OUTPUT]
    if output.get(CONF_TYPE) != OUTPUT_COMBINED:
        return {**options, CONF_OUTPUT: dict(block)}
    parts = [
        dict(block) if part.get(CONF_TYPE) == block[CONF_TYPE] else part
        for part in output.get(CONF_PARTS, [])
    ]
    return {**options, CONF_OUTPUT: {**output, CONF_PARTS: parts}}


def combine(blocks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The output block for these blocks: one alone, several combined."""
    if len(blocks) == 1:
        return dict(blocks[0])
    return {CONF_TYPE: OUTPUT_COMBINED, CONF_PARTS: [dict(block) for block in blocks]}
