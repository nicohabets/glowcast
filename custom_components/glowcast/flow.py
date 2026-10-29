"""What the setup and options steps of every source and output type share.

A type's steps are a mixin of `async_step_<id>` methods next to its adapter
(architecture 4.1). Home Assistant calls a step by its id, so the steps must be
methods of the one registered flow; `config_flow.py` combines them. A type
keeps its own state in attributes that start with its name, so two types never
share one.

The steps talk to the flow that runs them through what that flow provides:
- `entry_title`: a property with the name of the Glowcast entry (the TV's
  name);
- `async_source_chosen(source, title)`: the source's steps are done, with the
  source's block for `data["source"]` and the entry's title;
- `async_output_chosen(data, output)`: the output's steps are done, with what
  the output stores in `data` (for example its secrets) and its block for
  `options["output"]`.
"""

from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)


def dropdown(choices: dict[str, str]) -> SelectSelector:
    """A dropdown of {value: label}, sorted by label."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=value, label=label)
                for value, label in sorted(choices.items(), key=lambda item: item[1])
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )
