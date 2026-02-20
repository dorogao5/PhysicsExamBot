from src.bot.text_format import markdownish_to_telegram_html


def test_heading_and_emphasis_render() -> None:
    source = "# Заголовок\nТекст **жирный** и *курсив*"
    rendered = markdownish_to_telegram_html(source)
    assert "<b>Заголовок</b>" in rendered
    assert "<b>жирный</b>" in rendered
    assert "<i>курсив</i>" in rendered


def test_code_and_escape_render() -> None:
    source = "`a < b` и **x & y**"
    rendered = markdownish_to_telegram_html(source)
    assert "<code>a &lt; b</code>" in rendered
    assert "<b>x &amp; y</b>" in rendered


def test_latex_subscript_render() -> None:
    source = "D_{1n} = σ, E_{1n} и ε_{0}"
    rendered = markdownish_to_telegram_html(source)
    assert "D₁ₙ = σ, E₁ₙ и ε₀" in rendered


def test_latex_subscript_not_converted_in_code_or_unknown_content() -> None:
    source = "`D_{1n}` и E_{\\tau}"
    rendered = markdownish_to_telegram_html(source)
    assert "<code>D_{1n}</code>" in rendered
    assert "E_{\\tau}" in rendered
