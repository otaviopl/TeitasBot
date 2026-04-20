import json


def _normalize_json_text(raw_text):
    return raw_text.replace('\t', '').replace('\n', '').replace('\'', '\"')


def parse_chatgpt_message(message, project_logger):
    cleaned_message = (
        message.strip()
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )

    json_as_str = cleaned_message
    general_message = ""

    if "\n\n" in cleaned_message:
        possible_json, possible_comment = cleaned_message.rsplit("\n\n", 1)
        try:
            json.loads(_normalize_json_text(possible_json))
            json_as_str = possible_json
            general_message = possible_comment.strip().replace('/', '')
        except json.JSONDecodeError:
            json_as_str = cleaned_message

    project_logger.debug(json_as_str)

    try:
        json_obj = json.loads(_normalize_json_text(json_as_str))
        return json_obj, general_message
    except json.JSONDecodeError:
        project_logger.error("Failed to parse ChatGPT answer.")

    return None, None
