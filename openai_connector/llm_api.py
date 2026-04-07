import logging
import os
import datetime
import json
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import openai
from dotenv import load_dotenv

from utils.timezone_utils import (
    build_time_context,
    get_configured_timezone_name,
    today_in_configured_timezone,
    today_iso_in_configured_timezone,
)

load_dotenv()

_logger = logging.getLogger(__name__)

DEFAULT_LLM_MODEL = "gpt-4.1-mini"
DEFAULT_AUDIO_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
PROMPT_FILE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "openai_prompt.txt")
)
PROMPT_TEMPLATE_FILE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "openai_prompt_template.txt")
)
DEFAULT_PROMPT = (
    "Você é um assistente de produtividade e deve responder em português para mensagem do Telegram."
    "\nObjetivo: priorizar tarefas, explicar rapidamente os motivos e sugerir próximo passo."
    "\n\nFormato obrigatório da resposta (Markdown):"
    "\n## Prioridades de hoje"
    "\n- **[Alta] Nome da tarefa** — motivo curto + tempo estimado"
    "\n- **[Média] Nome da tarefa** — motivo curto + tempo estimado"
    "\n- **[Baixa] Nome da tarefa** — motivo curto + tempo estimado"
    "\n\n## Próximo passo recomendado"
    "\nUma frase objetiva com a melhor ação agora."
    "\n\nRegras:"
    "\n- Não responder em JSON."
    "\n- Seja breve e direto."
    "\n- Se não houver tarefa, escreva exatamente: \"Sem tarefas para priorizar hoje.\""
    "\n- Considere o campo Tags (ex.: TAKES TIME, FAST, FUP) para ajustar a priorização por esforço/contexto."
    "\n- Use o campo projeto para equilibrar prioridades entre contextos (ex.: Pessoal, Draiven, Monks)."
    "\n- Na lista final, para cada tarefa, mostre explicitamente o projeto e uma label de prazo: [ATRASADA] ou [NO PRAZO]."
    "\n\nTarefas:"
)
TASK_PARSER_PROMPT = (
    "Você recebe uma frase para criar uma tarefa e deve retornar somente JSON válido."
    "\nExtraia os campos: task_name, project, due_date, tags."
    "\nRegras:"
    "\n- due_date deve estar em formato YYYY-MM-DD."
    "\n- tags deve ser uma lista de strings."
    "\n- tags deve representar tipo/complexidade/contexto da tarefa (ex.: FAST, TAKES TIME, FOLLOWUP, DEEP WORK, ADMIN)."
    "\n- tags NÃO deve representar data/período/horário (ex.: amanhã, hoje, manhã, tarde, noite, segunda, urgente amanhã)."
    "\n- Se data não for informada, use a data de hoje."
    "\n- Se projeto não for informado, use \"Pessoal\"."
    "\n- Não inclua texto fora do JSON."
    "\nExemplo de formato:"
    "\n{\"task_name\":\"...\",\"project\":\"Pessoal\",\"due_date\":\"2026-03-01\",\"tags\":[\"FAST\"]}"
)
EVENT_PARSER_PROMPT = (
    "Você recebe uma frase para criar um evento no Google Calendar e deve retornar somente JSON válido."
    "\nExtraia os campos: summary, start_datetime, end_datetime, description, timezone."
    "\nRegras:"
    "\n- Corrija erros gramaticais e normalize o texto do usuário antes de preencher summary/description."
    "\n- O summary deve ser curto, claro e bem escrito."
    "\n- A description deve ser reescrita com gramática correta quando houver texto livre do usuário."
    "\n- start_datetime e end_datetime devem estar em formato YYYY-MM-DDTHH:MM."
    "\n- timezone deve ser um timezone IANA (ex.: America/Sao_Paulo)."
    "\n- Se descrição não for informada, use string vazia."
    "\n- Se timezone não for informado, use \"America/Sao_Paulo\"."
    "\n- Não inclua texto fora do JSON."
    "\nExemplo:"
    "\n{\"summary\":\"Reunião\",\"start_datetime\":\"2026-03-03T10:00\",\"end_datetime\":\"2026-03-03T11:00\",\"description\":\"Kickoff\",\"timezone\":\"America/Sao_Paulo\"}"
)
NOTE_PARSER_PROMPT = (
    "Você recebe uma frase para criar uma anotação e deve retornar somente JSON válido."
    "\nExtraia os campos: note_name, tag, observations, url."
    "\nRegras:"
    "\n- note_name deve ser curto e claro."
    "\n- tag deve ser coerente com o tema principal da anotação e conter uma única categoria."
    "\n- observations deve conter o conteúdo principal da anotação."
    "\n- url deve conter link válido (http/https) quando houver; caso contrário, string vazia."
    "\n- Não inclua texto fora do JSON."
    "\nExemplo:"
    "\n{\"note_name\":\"Ideia para onboarding\",\"tag\":\"IDEA\",\"observations\":\"Criar checklist inicial para novos clientes.\",\"url\":\"\"}"
)
NOTE_METADATA_PROMPT = (
    "Você recebe o conteúdo Markdown de uma anotação pessoal e deve retornar somente JSON válido."
    "\nGere dois campos: title e tags."
    "\nRegras:"
    "\n- title: um título curto e descritivo (máx. 60 caracteres) que resuma o conteúdo."
    "\n- tags: lista de 1 a 3 tags relevantes em minúsculas. Tags são palavras-chave curtas (1-2 palavras)."
    "\n- Use o mesmo idioma do conteúdo para título e tags."
    "\n- Se o conteúdo estiver vazio ou for muito curto, use title \"Nova anotação\" e tags []."
    "\n- Não inclua texto fora do JSON."
    "\nExemplo:"
    "\n{\"title\":\"Planejamento sprint Q2\",\"tags\":[\"trabalho\",\"sprint\",\"planejamento\"]}"
)
CALENDAR_SUMMARY_PROMPT = (
    "Você é um assistente e deve resumir eventos da agenda da semana para o assistente pessoal em português."
    "\nFormato obrigatório em Markdown:"
    "\n## Agenda da semana"
    "\n- **DD/MM HH:MM** — Evento (contexto curto)"
    "\n## Destaques"
    "\n- Linha com conflitos, blocos longos ou janela livre."
    "\nRegras:"
    "\n- Seja breve e útil."
    "\n- Ao listar eventos, não inclua links automaticamente; mostre apenas nome, dia e horário."
    "\n- Só inclua links ou detalhes extras de um evento quando o usuário pedir explicitamente."
    "\n- Se não houver eventos, responda exatamente: \"Sem eventos na agenda para os próximos 7 dias.\""
)
DAY_SUMMARY_PROMPT = (
    "Você é um assistente pessoal e deve gerar um resumo do dia em português."
    "\nFormato obrigatório em Markdown:"
    "\n## Resumo do dia"
    "\n### Tarefas de hoje"
    "\n- **HH:MM ou Dia inteiro** — Tarefa (Projeto) [Tags]"
    "\n### Agenda de hoje"
    "\n- **HH:MM ou Dia inteiro** — Evento (local opcional)"
    "\n### Foco recomendado"
    "\n- 1 a 3 bullets curtos com prioridade e próximo passo."
    "\nRegras:"
    "\n- Seja objetivo, claro e organizado."
    "\n- Não responder em JSON."
    "\n- Se não houver tarefas e nem eventos hoje, responda exatamente:"
    "\n\"## Resumo do dia\\n\\nSem tarefas e sem eventos para hoje.\""
)


class OpenAICallError(Exception):
    """Raised when an OpenAI API call fails after handling."""

    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message)
        self.original = original


def _safe_openai_call(callable_fn, *, description: str = "OpenAI API call"):
    """Execute *callable_fn* and translate openai exceptions into OpenAICallError."""
    try:
        return callable_fn()
    except openai.APITimeoutError as exc:
        _logger.error("%s timed out: %s", description, exc)
        raise OpenAICallError(
            f"O serviço de IA demorou demais para responder (timeout). Tente novamente.",
            original=exc,
        ) from exc
    except openai.RateLimitError as exc:
        _logger.warning("%s rate-limited: %s", description, exc)
        raise OpenAICallError(
            f"Limite de requisições à IA atingido. Aguarde alguns segundos e tente novamente.",
            original=exc,
        ) from exc
    except openai.AuthenticationError as exc:
        _logger.error("%s authentication failed: %s", description, exc)
        raise OpenAICallError(
            f"Falha de autenticação com o serviço de IA. Verifique a chave OPENAI_KEY.",
            original=exc,
        ) from exc
    except openai.APIConnectionError as exc:
        _logger.error("%s connection error: %s", description, exc)
        raise OpenAICallError(
            f"Não foi possível conectar ao serviço de IA. Verifique a conexão com a internet.",
            original=exc,
        ) from exc
    except openai.APIStatusError as exc:
        _logger.error("%s API status error (HTTP %s): %s", description, exc.status_code, exc)
        raise OpenAICallError(
            f"Erro no serviço de IA (HTTP {exc.status_code}). Tente novamente.",
            original=exc,
        ) from exc


def call_openai_assistant(tasks, project_logger):
    openai_client = _create_openai_client()
    llm_model = _get_llm_model()

    project_logger.info("Calling ChatGPT. This can take a while...")

    completion = _safe_openai_call(
        lambda: openai_client.responses.create(
            model=llm_model,
            input=build_message(tasks),
        ),
        description="call_openai_assistant",
    )

    answer = completion.output_text

    return answer


def parse_add_task_input(user_input, project_logger):
    openai_client = _create_openai_client()
    llm_model = _get_llm_model()

    project_logger.info("Calling LLM to parse add_task input...")
    completion = _safe_openai_call(
        lambda: openai_client.responses.create(
            model=llm_model,
            input=(
                f"{TASK_PARSER_PROMPT}\n\n"
                f"{_build_temporal_prompt_context()}\n\n"
                f"Input do usuário:\n{user_input}"
            ),
        ),
        description="parse_add_task_input",
    )
    return parse_add_task_output(completion.output_text)


def summarize_calendar_events(events, project_logger):
    if not events:
        return "Sem eventos na agenda para os próximos 7 dias."

    openai_client = _create_openai_client()
    llm_model = _get_llm_model()

    project_logger.info("Calling LLM to summarize calendar events...")
    completion = _safe_openai_call(
        lambda: openai_client.responses.create(
            model=llm_model,
            input=build_calendar_events_prompt(events),
        ),
        description="summarize_calendar_events",
    )
    return completion.output_text


def parse_add_event_input(user_input, project_logger):
    openai_client = _create_openai_client()
    llm_model = _get_llm_model()
    default_timezone = _get_default_event_timezone()

    project_logger.info("Calling LLM to parse add_event input...")
    completion = _safe_openai_call(
        lambda: openai_client.responses.create(
            model=llm_model,
            input=(
                f"{EVENT_PARSER_PROMPT}\n"
                f"\nDefault timezone para este usuário: {default_timezone}."
                f"\nSe timezone não for informado pelo usuário, use exatamente {default_timezone}.\n\n"
                f"{_build_temporal_prompt_context()}\n\n"
                f"Input do usuário:\n{user_input}"
            ),
        ),
        description="parse_add_event_input",
    )
    return parse_add_event_output(completion.output_text)


def parse_add_note_input(user_input, project_logger):
    openai_client = _create_openai_client()
    llm_model = _get_llm_model()

    project_logger.info("Calling LLM to parse add_note input...")
    completion = _safe_openai_call(
        lambda: openai_client.responses.create(
            model=llm_model,
            input=f"{NOTE_PARSER_PROMPT}\n\nInput do usuário:\n{user_input}",
        ),
        description="parse_add_note_input",
    )
    return parse_add_note_output(completion.output_text)


def generate_note_metadata(content: str, project_logger) -> dict:
    """Call LLM to generate title and tags from note content. Returns {"title": str, "tags": list[str]}."""
    content_trimmed = (content or "").strip()
    if len(content_trimmed) < 5:
        return {"title": "Nova anotação", "tags": []}

    openai_client = _create_openai_client()
    llm_model = _get_llm_model()

    # Limit content sent to LLM to avoid excessive token usage
    max_chars = 4000
    input_content = content_trimmed[:max_chars]

    project_logger.info("Calling LLM to generate note metadata...")
    completion = _safe_openai_call(
        lambda: openai_client.responses.create(
            model=llm_model,
            input=f"{NOTE_METADATA_PROMPT}\n\nConteúdo da anotação:\n{input_content}",
        ),
        description="generate_note_metadata",
    )

    raw = completion.output_text.strip()
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        project_logger.warning("LLM returned invalid JSON for note metadata: %s", raw[:200])
        return {"title": "Nova anotação", "tags": []}

    title = str(data.get("title", "Nova anotação")).strip()[:60] or "Nova anotação"
    raw_tags = data.get("tags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = [str(t).strip().lower() for t in raw_tags[:3] if str(t).strip()]

    return {"title": title, "tags": tags}


def estimate_calories(description: str, category: str = "meal", logger=None) -> float | None:
    """Use LLM to estimate calories for a meal or exercise.

    Args:
        description: Human-readable description (e.g. "200g arroz branco" or "corrida 30 min").
        category: "meal" for food calories, "exercise" for calories burned.
        logger: Optional logger instance.

    Returns:
        Estimated calories as float, or None if estimation fails.
    """
    description = (description or "").strip()
    if not description:
        return None

    openai_client = _create_openai_client()
    llm_model = _get_llm_model()

    if category == "exercise":
        prompt = (
            "Estime as calorias gastas na seguinte atividade física. "
            "Considere uma pessoa adulta de ~75 kg. "
            "Responda APENAS com um número inteiro representando as kcal gastas. "
            "Sem texto adicional, sem unidade — apenas o número.\n\n"
            f"Atividade: {description}"
        )
    else:
        prompt = (
            "Estime as calorias (kcal) da seguinte refeição/alimento na quantidade indicada. "
            "Responda APENAS com um número inteiro representando as kcal. "
            "Sem texto adicional, sem unidade — apenas o número.\n\n"
            f"Alimento: {description}"
        )

    try:
        completion = _safe_openai_call(
            lambda: openai_client.responses.create(model=llm_model, input=prompt),
            description="estimate_calories",
        )
        raw = completion.output_text.strip().replace(",", ".").replace("kcal", "").strip()
        # Extract first number-like token
        import re as _re
        match = _re.search(r"[\d]+(?:[.,]\d+)?", raw)
        if match:
            value = float(match.group().replace(",", "."))
            if value > 0:
                return round(value, 1)
    except Exception:
        if logger:
            logger.warning("Failed to estimate calories via LLM for: %s", description)
    return None


_NUTRITIONAL_ANALYSIS_PROMPT = (
    "Você é um nutricionista esportivo profissional. Analise os dados de alimentação e exercícios "
    "dos últimos 7 dias fornecidos abaixo e produza uma análise nutricional completa e detalhada em português.\n\n"
    "A análise deve conter:\n"
    "1. **Resumo calórico** — média diária consumida vs. queimada, tendência da semana\n"
    "2. **Macronutrientes estimados** — estimativa de proteínas, carboidratos e gorduras com base nos alimentos listados\n"
    "3. **Micronutrientes relevantes** — análise de cálcio, ferro, vitaminas A, C, D, B12, magnésio, zinco, etc. "
    "baseado nos alimentos consumidos\n"
    "4. **Fibras e hidratação** — estimativa de fibras com base nos alimentos\n"
    "5. **Equilíbrio dos exercícios** — calorias gastas, frequência, variedade, adequação ao consumo calórico\n"
    "6. **Pontos positivos** — o que está indo bem\n"
    "7. **Pontos de atenção** — deficiências prováveis, excessos, desequilíbrios\n"
    "8. **Recomendações** — sugestões práticas para melhorar a dieta e os exercícios\n\n"
    "Use formatação Markdown (títulos, listas, negrito). Seja específico e baseado nos dados reais fornecidos. "
    "Se não houver dados suficientes para uma seção, indique isso claramente."
)


def generate_nutritional_analysis(meals: list[dict], exercises: list[dict], logger=None) -> str:
    """Call LLM to produce a detailed 7-day nutritional analysis.

    Args:
        meals: List of meal dicts with keys: food, meal_type, quantity, calories, date.
        exercises: List of exercise dicts with keys: activity, calories, duration_minutes, date.
        logger: Optional logger instance.

    Returns:
        Markdown-formatted analysis string.
    """
    if not meals and not exercises:
        return "Sem dados de refeições ou exercícios nos últimos 7 dias para analisar."

    # Build data summary for the prompt
    lines = []
    lines.append("## Refeições dos últimos 7 dias\n")
    if meals:
        for m in meals:
            food = m.get("food", "?")
            meal_type = m.get("meal_type", "?")
            qty = m.get("quantity", "")
            cal = m.get("calories", 0)
            date = str(m.get("date", ""))[:10]
            lines.append(f"- [{date}] {meal_type}: {food} ({qty}) — {cal} kcal")
    else:
        lines.append("_Nenhuma refeição registrada._")

    lines.append("\n## Exercícios dos últimos 7 dias\n")
    if exercises:
        for e in exercises:
            activity = e.get("activity", "?")
            cal = e.get("calories", 0)
            dur = e.get("duration_minutes")
            date = str(e.get("date", ""))[:10]
            dur_str = f", {dur} min" if dur else ""
            lines.append(f"- [{date}] {activity} — {cal} kcal{dur_str}")
    else:
        lines.append("_Nenhum exercício registrado._")

    data_text = "\n".join(lines)
    # Limit to avoid excessive tokens
    if len(data_text) > 8000:
        data_text = data_text[:8000] + "\n... (dados truncados)"

    openai_client = _create_openai_client()
    llm_model = _get_llm_model()

    full_prompt = f"{_NUTRITIONAL_ANALYSIS_PROMPT}\n\n---\n\n{data_text}"

    if logger:
        logger.info("Calling LLM for nutritional analysis (%d meals, %d exercises)...", len(meals), len(exercises))

    try:
        completion = _safe_openai_call(
            lambda: openai_client.responses.create(model=llm_model, input=full_prompt),
            description="generate_nutritional_analysis",
        )
        return completion.output_text.strip()
    except OpenAICallError as exc:
        if logger:
            logger.error("Nutritional analysis LLM call failed: %s", exc)
        return f"Erro ao gerar análise nutricional: {exc}"


def transcribe_audio_input(audio_bytes, filename, mime_type, project_logger):
    if not audio_bytes:
        raise ValueError("audio_bytes is required")

    openai_client = _create_openai_client()
    transcribe_model = _get_audio_transcribe_model()
    safe_filename = str(filename or "audio_input.bin")
    if "." not in safe_filename:
        safe_filename = f"{safe_filename}.bin"
    safe_mime_type = str(mime_type or "application/octet-stream").strip() or "application/octet-stream"

    project_logger.info("Calling LLM to transcribe audio input...")
    transcription = _safe_openai_call(
        lambda: openai_client.audio.transcriptions.create(
            model=transcribe_model,
            file=(safe_filename, audio_bytes, safe_mime_type),
        ),
        description="transcribe_audio_input",
    )

    transcript_text = str(getattr(transcription, "text", "") or "").strip()
    if not transcript_text and isinstance(transcription, dict):
        transcript_text = str(transcription.get("text", "")).strip()
    if not transcript_text:
        raise ValueError("LLM returned empty audio transcription")
    return transcript_text


def summarize_day_context(today_tasks, today_events, project_logger):
    return summarize_period_context("hoje", today_tasks, today_events, project_logger)


def summarize_period_context(period_label, tasks, events, project_logger):
    if not tasks and not events:
        return _build_empty_period_message(period_label)

    openai_client = _create_openai_client()
    llm_model = _get_llm_model()

    project_logger.info("Calling LLM to summarize %s context...", period_label)
    completion = _safe_openai_call(
        lambda: openai_client.responses.create(
            model=llm_model,
            input=build_period_summary_prompt(period_label, tasks, events),
        ),
        description=f"summarize_period_context({period_label})",
    )
    return completion.output_text


def parse_add_task_output(output_text):
    payload = _extract_json_payload(output_text)
    task_name = str(payload.get("task_name", "")).strip()
    project = str(payload.get("project", "Pessoal")).strip() or "Pessoal"
    due_date = str(payload.get("due_date", "")).strip()
    tags = payload.get("tags", [])

    if not task_name:
        raise ValueError("LLM did not provide task_name")
    if not due_date:
        due_date = today_iso_in_configured_timezone()
    try:
        datetime.date.fromisoformat(due_date)
    except ValueError as error:
        raise ValueError("LLM did not provide a valid due_date (YYYY-MM-DD)") from error
    if not isinstance(tags, list):
        raise ValueError("LLM did not provide tags as a list")

    clean_tags = _sanitize_task_tags(tags)
    return {
        "task_name": task_name,
        "project": project,
        "due_date": due_date,
        "tags": clean_tags,
    }


def parse_add_event_output(output_text):
    payload = _extract_json_payload(output_text)
    summary = str(payload.get("summary", "")).strip()
    start_datetime = str(payload.get("start_datetime", "")).strip()
    end_datetime = str(payload.get("end_datetime", "")).strip()
    description = str(payload.get("description", "")).strip()
    default_timezone = _get_default_event_timezone()
    timezone = str(payload.get("timezone", default_timezone)).strip() or default_timezone

    if not summary:
        raise ValueError("LLM did not provide summary")
    _validate_event_datetime(start_datetime, "start_datetime")
    _validate_event_datetime(end_datetime, "end_datetime")

    if end_datetime <= start_datetime:
        raise ValueError("LLM provided end_datetime before or equal to start_datetime")

    return {
        "summary": summary,
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
        "description": description,
        "timezone": timezone,
    }


def parse_add_note_output(output_text):
    payload = _extract_json_payload(output_text)
    note_name = str(payload.get("note_name") or payload.get("name") or "").strip()
    tag = str(payload.get("tag", "")).strip()
    observations = str(payload.get("observations") or payload.get("notes") or "").strip()
    url = str(payload.get("url", "")).strip()

    if not note_name:
        raise ValueError("LLM did not provide note_name")
    if not tag:
        tag = _infer_note_tag(f"{note_name} {observations}")

    return {
        "note_name": note_name,
        "tag": tag,
        "observations": observations,
        "url": url,
    }


def _format_task_for_prompt(task):
    tags = ", ".join(task.get("tags", [])) if task.get("tags") else "sem tag"
    project = task.get("project", "No project")
    deadline = task.get("deadline", "sem data")
    overdue_label = _build_overdue_label(deadline)
    return (
        f"\n - {task['name']} | projeto: {project} | prazo: {deadline}"
        f" | status_prazo: {overdue_label} | tags: {tags}"
    )


def _build_overdue_label(deadline):
    if not deadline:
        return "NO PRAZO"
    try:
        deadline_date = datetime.date.fromisoformat(str(deadline).split("T")[0])
        return "ATRASADA" if deadline_date < today_in_configured_timezone() else "NO PRAZO"
    except ValueError:
        return "NO PRAZO"


def _extract_json_payload(output_text):
    text = str(output_text or "").strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM did not return valid JSON")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as error:
            raise ValueError("LLM did not return valid JSON") from error


def _sanitize_task_tags(tags):
    blocked_terms = {
        "amanha", "amanhã", "hoje", "ontem",
        "manha", "manhã", "tarde", "noite",
        "segunda", "terca", "terça", "quarta", "quinta", "sexta", "sabado", "sábado", "domingo",
    }
    clean = []
    for tag in tags:
        value = str(tag).strip()
        if not value:
            continue
        normalized = value.lower()
        if normalized in blocked_terms:
            continue
        clean.append(value)
    return clean


def _infer_note_tag(content):
    text = str(content or "").lower()
    if any(keyword in text for keyword in ("reunião", "reuniao", "meeting", "call", "1:1")):
        return "MEETING"
    if any(keyword in text for keyword in ("ideia", "idea", "brainstorm", "insight")):
        return "IDEA"
    if any(keyword in text for keyword in ("bug", "erro", "falha", "issue")):
        return "BUG"
    if any(keyword in text for keyword in ("estudo", "study", "curso", "aprender", "learn")):
        return "STUDY"
    if any(keyword in text for keyword in ("saúde", "saude", "treino", "health")):
        return "HEALTH"
    return "GENERAL"


def _validate_event_datetime(value, field_name):
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError as error:
        raise ValueError(f"LLM did not provide a valid {field_name} (YYYY-MM-DDTHH:MM)") from error


def build_calendar_events_prompt(events):
    event_lines = "".join(
        f"\n - {event.get('summary', 'Sem título')} | start: {event.get('start')} | end: {event.get('end')} | location: {event.get('location') or 'N/A'}"
        for event in events
    )
    return f"{CALENDAR_SUMMARY_PROMPT}\n\nEventos:{event_lines}"


def build_day_summary_prompt(today_tasks, today_events):
    return build_period_summary_prompt("hoje", today_tasks, today_events)


def build_period_summary_prompt(period_label, tasks, events):
    period_key = str(period_label or "hoje").strip().lower()
    summary_title = _build_period_summary_title(period_key)
    empty_target = _build_period_empty_target(period_key)
    empty_message = _build_empty_period_message(period_key)
    section_tasks = _build_period_task_section_title(period_key)
    section_events = _build_period_event_section_title(period_key)
    temporal_context = _build_temporal_prompt_context()

    if _is_week_period(period_key):
        prompt_header = (
            f"Você é um assistente pessoal e deve gerar um resumo {summary_title} em português."
            "\nFormato obrigatório em Markdown:"
            f"\n## Resumo {summary_title}"
            f"\n### {section_tasks}"
            "\n- 3 a 6 bullets curtos agrupando prioridades por contexto/projeto."
            f"\n### {section_events}"
            "\n- 3 a 6 bullets com blocos importantes, conflitos e janelas livres."
            "\n### Foco recomendado"
            "\n- 1 a 3 bullets curtos com prioridade e próximo passo."
            "\nRegras:"
            "\n- Seja objetivo, claro e organizado."
            "\n- Não responder em JSON."
            "\n- Não liste todas as tarefas ou eventos individualmente; sintetize os principais pontos."
            f"\n- Se não houver tarefas e nem eventos para {empty_target}, responda exatamente:"
            f"\n\"{empty_message.replace(chr(10), '\\\\n')}\""
            f"\n\n{temporal_context}"
        )
    else:
        prompt_header = (
            f"Você é um assistente pessoal e deve gerar um resumo {summary_title} em português."
            "\nFormato obrigatório em Markdown:"
            f"\n## Resumo {summary_title}"
            f"\n### {section_tasks}"
            "\n- **HH:MM ou Dia inteiro** — Tarefa (Projeto) [Tags]"
            f"\n### {section_events}"
            "\n- **HH:MM ou Dia inteiro** — Evento (local opcional)"
            "\n### Foco recomendado"
            "\n- 1 a 3 bullets curtos com prioridade e próximo passo."
            "\nRegras:"
            "\n- Seja objetivo, claro e organizado."
            "\n- Não responder em JSON."
            f"\n- Se não houver tarefas e nem eventos para {empty_target}, responda exatamente:"
            f"\n\"{empty_message.replace(chr(10), '\\\\n')}\""
            f"\n\n{temporal_context}"
        )

    task_lines = "".join(
        f"\n - {task.get('name', 'Sem nome')} | projeto: {task.get('project', 'Sem projeto')} | "
        f"deadline: {task.get('deadline', 'sem data')} | tags: {', '.join(task.get('tags', [])) if task.get('tags') else 'sem tags'}"
        for task in tasks
    ) or "\n - Sem tarefas"

    event_lines = "".join(
        f"\n - {event.get('summary', 'Sem título')} | start: {event.get('start', 'sem início')} | "
        f"end: {event.get('end', 'sem fim')} | location: {event.get('location') or 'N/A'}"
        for event in events
    ) or "\n - Sem eventos"

    return f"{prompt_header}\n\n{section_tasks}:{task_lines}\n\n{section_events}:{event_lines}"


def _build_empty_period_message(period_key):
    summary_title = _build_period_summary_title(period_key)
    empty_target = _build_period_empty_target(period_key)
    return f"## Resumo {summary_title}\n\nSem tarefas e sem eventos para {empty_target}."


def _build_period_summary_title(period_key):
    if period_key in ("amanha", "amanhã", "tomorrow"):
        return "de amanhã"
    if period_key in ("semana", "semana atual", "week"):
        return "da semana atual"
    return "de hoje"


def _build_period_empty_target(period_key):
    if period_key in ("amanha", "amanhã", "tomorrow"):
        return "amanhã"
    if period_key in ("semana", "semana atual", "week"):
        return "esta semana"
    return "hoje"


def _build_period_task_section_title(period_key):
    if period_key in ("amanha", "amanhã", "tomorrow"):
        return "Tarefas de amanhã"
    if period_key in ("semana", "semana atual", "week"):
        return "Tarefas da semana"
    return "Tarefas de hoje"


def _build_period_event_section_title(period_key):
    if period_key in ("amanha", "amanhã", "tomorrow"):
        return "Agenda de amanhã"
    if period_key in ("semana", "semana atual", "week"):
        return "Agenda da semana"
    return "Agenda de hoje"


def _is_week_period(period_key):
    return period_key in ("semana", "semana atual", "week")


def _get_default_event_timezone():
    configured_timezone = get_configured_timezone_name()
    try:
        ZoneInfo(configured_timezone)
        return configured_timezone
    except ZoneInfoNotFoundError:
        return "America/Sao_Paulo"


def _build_temporal_prompt_context():
    time_context = build_time_context()
    return (
        "Contexto temporal operacional do usuário:\n"
        f"- Timezone: {time_context['timezone_name']} (UTC offset {time_context['local_utc_offset']})\n"
        f"- Data local atual: {time_context['local_date_iso']}\n"
        f"- Horário local atual: {time_context['local_now_iso']}\n"
        "- Regra: interprete termos relativos de tempo (hoje, amanhã, agora, esta semana) usando este timezone."
    )


def build_message(tasks):
    task_lines = "".join(_format_task_for_prompt(task) for task in tasks)

    for prompt_path in (PROMPT_FILE_PATH, PROMPT_TEMPLATE_FILE_PATH):
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as prompt_file:
                prompt = prompt_file.read().rstrip()
            return f"{prompt}{task_lines}"

    return f"{DEFAULT_PROMPT}{task_lines}"


_openai_client_lock = threading.Lock()
_openai_client_instance: openai.OpenAI | None = None


def _create_openai_client():
    """Return a shared OpenAI client, creating it on first call."""
    global _openai_client_instance
    if _openai_client_instance is not None:
        return _openai_client_instance
    with _openai_client_lock:
        if _openai_client_instance is not None:
            return _openai_client_instance
        openai_api_key = os.getenv("OPENAI_KEY")
        if not openai_api_key:
            raise ValueError("Missing required environment variable: OPENAI_KEY")
        timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60"))
        _openai_client_instance = openai.OpenAI(
            api_key=openai_api_key, timeout=timeout_seconds,
        )
        return _openai_client_instance


def _get_llm_model():
    llm_model = str(os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)).strip()
    return llm_model or DEFAULT_LLM_MODEL


def _get_audio_transcribe_model():
    transcribe_model = str(os.getenv("AUDIO_TRANSCRIBE_MODEL", DEFAULT_AUDIO_TRANSCRIBE_MODEL)).strip()
    return transcribe_model or DEFAULT_AUDIO_TRANSCRIBE_MODEL
