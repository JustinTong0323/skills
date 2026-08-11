import re
import tomllib


LOOP_SECTION = re.compile(r"(?m)^[ \t]*\[loop_control\][ \t]*(?:#.*)?$")
TABLE_SECTION = re.compile(r"(?m)^[ \t]*\[\[?.+?\]?\][ \t]*(?:#.*)?$")


def _key_pattern(key):
    forms = (re.escape(key), re.escape(f'"{key}"'), re.escape(f"'{key}'"))
    return re.compile(
        rf"(?m)^(?P<indent>[ \t]*)(?:{'|'.join(forms)})"
        rf"(?P<separator>[ \t]*=[ \t]*)[^#\r\n]*"
        rf"(?P<comment>[ \t]*#.*)?$"
    )


def merge_loop_control(config_text, max_steps_per_turn=None, max_retries_per_step=None):
    parsed = tomllib.loads(config_text)
    values = {
        key: value
        for key, value in (
            ("max_steps_per_turn", max_steps_per_turn),
            ("max_retries_per_step", max_retries_per_step),
        )
        if value is not None
    }
    if not values:
        return config_text

    section = LOOP_SECTION.search(config_text)
    if section is None:
        if not config_text:
            separator = ""
        elif config_text.endswith("\n"):
            separator = "\n"
        else:
            separator = "\n\n"
        assignments = "\n".join(f"{key} = {value}" for key, value in values.items())
        merged = f"{config_text}{separator}[loop_control]\n{assignments}\n"
    else:
        next_section = TABLE_SECTION.search(config_text, section.end())
        section_end = next_section.start() if next_section else len(config_text)
        body = config_text[section.end() : section_end]
        existing = parsed.get("loop_control", {})
        for key, value in values.items():
            pattern = _key_pattern(key)
            match = pattern.search(body)
            if match:
                replacement = (
                    f"{match.group('indent')}{key}{match.group('separator')}{value}"
                    f"{match.group('comment') or ''}"
                )
                body = body[: match.start()] + replacement + body[match.end() :]
            elif key in existing:
                raise ValueError(f"cannot locate loop_control.{key} in config text")
            else:
                prefix = "" if body.endswith("\n") else "\n"
                body = f"{body}{prefix}{key} = {value}\n"
        merged = config_text[: section.end()] + body + config_text[section_end:]

    effective = tomllib.loads(merged)
    loop_control = effective.get("loop_control", {})
    for key, value in values.items():
        if loop_control.get(key) != value:
            raise ValueError(f"failed to set loop_control.{key}")
    return merged
