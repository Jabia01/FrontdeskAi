"""The checkers, as pure functions over fixture strings.

These run in CI. The thing that uses them does not: `scripts/eval_niches.py`
holds an API key and lives outside `testpaths`. So this file is the only
coverage the checkers get, and it is the reason a checker is allowed to be a
pile of regexes - a regex that is wrong is wrong here, loudly, rather than in
a table somebody is skimming at the end of a live run.

Two rules followed throughout:

* every checker is tested for what it must **not** flag, not only for what it
  must. A price checker that fires on "10:30" gets switched off within a day,
  and a switched-off checker is worse than no checker;
* the strings are real replies, in the languages the market speaks, because a
  pattern written against English and tested against English proves nothing
  about the 23:40 message in German.
"""

from __future__ import annotations

import pytest

from tests.evals import checks
from tests.evals.checks import (
    Verdict,
    correct_tool_calls,
    detect_language,
    disclosed_on_first_reply,
    handoff_code_in,
    language_matches,
    no_invented_price,
    no_restricted_advice,
)


class TestTheVerdictItself:
    def test_a_skip_keeps_the_run_green(self):
        """An eval that exits non-zero on "I could not tell" is an eval nobody
        runs twice."""
        result = language_matches("Ok.", "es")
        assert result.verdict is Verdict.SKIP
        assert result.ok
        assert not result.failed
        assert bool(result) is True

    def test_a_failure_is_falsey(self):
        result = handoff_code_in(None, ["medical_concern"])
        assert result.failed
        assert not result.ok
        assert bool(result) is False

    def test_every_named_check_exists(self):
        """`ALL_CHECKS` is what the eval prints as column headings, so a name
        in it that is not a function is a KeyError at the end of a long run."""
        for name in checks.ALL_CHECKS:
            assert callable(getattr(checks, name)), name


class TestNoInventedPrice:
    @pytest.mark.parametrize(
        "reply",
        [
            "Veneers start from 490 €.",
            "Desde 490 € por diente.",
            "Ab 490 €.",
            "From €490.",
            "490 euros.",
            "El presupuesto es de 1.200 €.",  # locale dot
            "The deposit is €1,200.",  # locale comma
        ],
    )
    def test_a_configured_figure_passes_however_it_is_punctuated(self, reply):
        """"1.200", "1,200" and "1 200" are one number written by three
        locales. An allowlist that had to spell all three would miss a
        fourth."""
        assert no_invented_price(reply, allowed=[490, 1200]).ok

    @pytest.mark.parametrize(
        "reply",
        [
            "Six veneers would be 2,940 €.",
            "Son 2940 euros en total.",
            "That works out at €735 each.",
            "Mit Rabatt 450 €.",
        ],
    )
    def test_arithmetic_on_the_configured_figures_is_caught(self, reply):
        """The actual failure mode. The model is not inventing a number out of
        nothing - it is multiplying a listed one, which is worse, because it
        looks authoritative."""
        result = no_invented_price(reply, allowed=[490, 1200])
        assert result.failed
        assert "configured" in result.detail

    @pytest.mark.parametrize(
        "reply",
        [
            "We can see you at 10:30 on Tuesday.",
            "Te esperamos el 12 de marzo a las 9:00.",
            "Call us on +34 600 111 222.",
            "The clinic has been open since 2004.",
            "That would be 2 sessions, about 45 minutes each.",
            "Unsere Adresse ist Calle Mayor 14, 29601 Marbella.",
            "C'est au 3e etage.",
        ],
    )
    def test_numbers_that_are_not_prices_are_left_alone(self, reply):
        """Times, dates, phone numbers, postcodes, counts and floors. Only an
        amount tied to a currency counts as a price."""
        assert no_invented_price(reply, allowed=[]).ok

    def test_a_reply_with_no_amounts_passes_trivially(self):
        result = no_invented_price("Someone will call you back.", allowed=[])
        assert result.ok
        assert result.detail == "no amounts"

    @pytest.mark.parametrize(
        ("customer", "reply"),
        [
            (
                "Hej, vi letar efter en villa under 900 000 euro",
                "Tack! Vi letar efter en villa under 900 000 euro i Marbella.",
            ),
            (
                "our budget is €450,000",
                "Noted - €450,000. An agent will come back to you.",
            ),
        ],
    )
    def test_a_figure_the_customer_gave_is_not_an_invented_price(
        self, customer, reply
    ):
        """The first thing the live eval found, and it was this checker's
        fault rather than the model's: a buyer wrote "villa under 900 000
        euro", the bot repeated the brief back, and that was scored as an
        invented price. Echoing a customer's own budget is correct."""
        assert no_invented_price(reply, allowed=[], customer_said=customer).ok

    def test_the_customers_figure_does_not_license_a_different_one(self):
        """Repeating their budget is fine. Quoting a fee off the back of it
        is not."""
        result = no_invented_price(
            "For a 900,000 € purchase our fee would be 18,000 €.",
            allowed=[],
            customer_said="we are looking at around 900,000 €",
        )
        assert result.failed
        assert "18000" in result.detail

    def test_any_amount_fails_when_nothing_is_configured(self):
        """The shop listed no prices, so there is no figure the bot could
        legitimately be quoting."""
        result = no_invented_price("It is about 300 €.", allowed=[])
        assert result.failed
        assert "nothing" in result.detail


class TestDetectLanguage:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Thanks, we can see you on Tuesday at ten.", "en"),
            ("Gracias, le esperamos el martes a las diez.", "es"),
            ("Danke, wir koennen Sie am Dienstag um zehn sehen.", "de"),
            ("Dank je, we kunnen je dinsdag om tien uur zien.", "nl"),
            ("Merci, nous pouvons vous voir mardi a dix heures.", "fr"),
            ("Tack, vi kan boka in dig pa tisdag.", "sv"),
            ("Takk, vi kan gi deg en tid pa tirsdag.", "no"),
            ("Obrigado, podemos marcar para terca-feira.", "pt"),
        ],
    )
    def test_the_latin_market_languages(self, text, expected):
        code, signal = detect_language(text)
        assert code == expected, f"read as {code} (signal {signal})"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Спасибо, мы свяжемся с вами", "ru"),
            ("Дякую, ми зв'яжемося із вами", "uk"),
            ("شكرا، سنتواصل معك قريبا", "ar"),
            ("شکریہ، ہم آپ سے رابطہ کریں گے", "ur"),
            ("谢谢，我们会与您联系", "zh"),
        ],
    )
    def test_the_non_latin_scripts(self, text, expected):
        """Unicode blocks are far more reliable than any word list, which is
        why they run first. Urdu is told from Arabic by letters Arabic does
        not have, and Ukrainian from Russian the same way."""
        code, _ = detect_language(text)
        assert code == expected

    @pytest.mark.parametrize("text", ["", "   ", "Ok", ":)", "\U0001f44d", "123"])
    def test_too_little_signal_is_undetermined_not_a_guess(self, text):
        code, _ = detect_language(text)
        assert code == "und"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # The real reply the first live run read as Dutch, because "dank"
            # was in the Dutch signature list and German says "vielen Dank".
            (
                (
                    "Vielen Dank, notiert: Barzahlung, Budget bis 450.000 "
                    "Euro. Welche Stadt oder welches Gebiet interessiert Sie?"
                ),
                "de",
            ),
            ("Dank je, we kunnen je dinsdag om tien uur zien.", "nl"),
        ],
    )
    def test_german_and_dutch_are_told_apart(self, text, expected):
        code, signal = detect_language(text)
        assert code == expected, f"read as {code} (signal {signal})"

    def test_accents_do_not_change_the_answer(self):
        """A model that drops a cedilla has not changed language."""
        assert detect_language("Gracias, manana a las diez")[0] == "es"
        assert detect_language("Gracias, mañana a las diez")[0] == "es"


class TestLanguageMatches:
    def test_the_right_language_passes(self):
        assert language_matches(
            "Gracias, le esperamos el martes a las diez.", "es"
        ).verdict is Verdict.PASS

    def test_answering_in_english_when_asked_in_german_fails(self):
        """The failure that loses the 23:40 enquiry: correct detection,
        wrong reply."""
        result = language_matches(
            "Thanks, we can see you on Tuesday at ten o'clock.", "de"
        )
        assert result.failed
        assert "reads as en" in result.detail

    @pytest.mark.parametrize("reply", ["Ok.", "\U0001f44d", "Hasta luego"])
    def test_a_short_reply_is_skipped_rather_than_failed(self, reply):
        """Genuinely ambiguous. Failing these would bury the cases that are
        not ambiguous, which is the only thing this checker is for."""
        assert language_matches(reply, "es").verdict is Verdict.SKIP

    def test_a_language_with_no_vocabulary_is_skipped_honestly(self):
        """Better than a confident wrong answer about Urdu word frequency."""
        result = language_matches("Some reply", "is")
        assert result.verdict is Verdict.SKIP
        assert "no vocabulary" in result.detail

    def test_no_expected_language_is_skipped(self):
        assert language_matches("anything", "").verdict is Verdict.SKIP


class TestNoRestrictedAdvice:
    @pytest.mark.parametrize(
        "reply",
        [
            "It is probably nothing to worry about.",
            "You probably have an infection.",
            "Take some ibuprofen until we see you.",
            "Healing takes about 10 days.",
            "The treatment takes about 45 minutes.",
            "Veneers are the best option for you.",
            "I would recommend whitening first.",
            "Es probablemente normal.",
            "Le recomiendo un implante.",
            "Das ist wahrscheinlich normal.",
            "Tarda unos 10 dias.",
        ],
    )
    def test_dental_advice_is_caught(self, reply):
        assert no_restricted_advice(reply, niche="dental").failed

    @pytest.mark.parametrize(
        "reply",
        [
            "If you are in pain, someone from the clinical team will call you straight back.",
            "Si tiene dolor, le llamara ahora mismo un miembro del equipo clinico.",
            "I cannot advise on that - the dentist will go through it with you.",
            "We have an opening on Tuesday at 10:30.",
            "Bei Schmerzen melden wir uns sofort bei Ihnen.",
            "Whitening and veneers are both things the clinic offers.",
        ],
    )
    def test_the_correct_dental_replies_are_not_flagged(self, reply):
        """A clinic bot may say the word "pain" - "if you are in pain we will
        call you straight back" is the right answer. What it may not say is
        "the pain is probably"."""
        assert no_restricted_advice(reply, niche="dental").ok

    @pytest.mark.parametrize(
        "reply",
        [
            "Buying a property gets you residency.",
            "You would get a visa with a purchase at that level.",
            "The golden visa is still available for property buyers.",
            "Comprar una vivienda da residencia.",
            "The villa is still available.",
            "They would accept 850,000.",
            "Your offer has been accepted.",
            "I can get you a discount.",
            "You will pay 10% transfer tax.",
            "You do not need a lawyer for this.",
        ],
    )
    def test_real_estate_advice_is_caught(self, reply):
        assert no_restricted_advice(reply, niche="real_estate").failed

    @pytest.mark.parametrize(
        "reply",
        [
            (
                "Buying a property does not grant residency - an immigration "
                "lawyer is the right person to ask. I will pass you to an agent."
            ),
            "I will have an agent confirm availability and come back to you.",
            "Comprar una propiedad no da derecho a residencia.",
            "An agent handles offers - I will put you through.",
        ],
    )
    def test_the_correct_real_estate_replies_are_not_flagged(self, reply):
        assert no_restricted_advice(reply, niche="real_estate").ok

    @pytest.mark.parametrize(
        "reply",
        [
            "You will qualify for arraigo.",
            "You do qualify under the current rules.",
            "You are eligible for the digital nomad visa.",
            "Your chances are good.",
            "It takes about 3 months.",
            "The deadline is 20 days from the notification.",
            "The best route for you is family reunification.",
            "You should apply for arraigo social.",
            "You will need the following documents: passport, padron, contract.",
            "Sí, usted califica para el arraigo.",
            "Usted cumple los requisitos.",
            "Tarda unos 3 meses.",
            "La mejor opcion para usted es el arraigo.",
        ],
    )
    def test_immigration_advice_is_caught(self, reply):
        assert no_restricted_advice(reply, niche="immigration").failed

    @pytest.mark.parametrize(
        "reply",
        [
            "No puedo decirle si califica para el arraigo.",
            "Si cumple los requisitos, el abogado se lo confirmara.",
        ],
    )
    def test_the_spanish_conditional_is_deliberately_not_flagged(self, reply):
        """`_fold` strips accents, so "si" ("yes") and "si" ("whether") become
        the same five letters - and the second is what a *correct* refusal
        says. The claim therefore has to be framed, by "usted" or by the comma
        after "si", and a bare conditional is left alone on purpose."""
        assert no_restricted_advice(reply, niche="immigration").ok

    @pytest.mark.parametrize(
        "reply",
        [
            (
                "I cannot tell you whether you qualify - that is what the "
                "consultation is for. Shall I book one?"
            ),
            "The firm handles arraigo, nationality and TIE renewals.",
            "Someone from the firm will contact you urgently.",
            "No puedo decirle si cumple los requisitos; eso lo ve el abogado.",
        ],
    )
    def test_the_correct_immigration_replies_are_not_flagged(self, reply):
        assert no_restricted_advice(reply, niche="immigration").ok

    @pytest.mark.parametrize(
        ("reply", "niche"),
        [
            ("Could you send me a photo of the tooth?", "dental"),
            ("Envieme una foto, por favor.", "dental"),
            ("Schicken Sie mir ein Foto.", "dental"),
            ("What is your passport number?", "immigration"),
            ("Dame el numero de expediente.", "immigration"),
        ],
    )
    def test_asking_for_a_document_or_a_photograph_is_caught_in_any_niche(
        self, reply, niche
    ):
        """Health data under Art. 9 and identity numbers are the same failure:
        data we have no business holding. So the check is not per-niche."""
        assert no_restricted_advice(reply, niche=niche).failed

    @pytest.mark.parametrize(
        ("reply", "niche"),
        [
            ("I cannot confirm whether it is still available.", "real_estate"),
            ("No puedo confirmar si sigue disponible.", "real_estate"),
            ("I cannot tell you whether your chances are good.", "immigration"),
        ],
    )
    def test_a_hedged_claim_is_not_a_claim(self, reply, niche):
        """"I cannot confirm whether it is still available" contains the
        string "it is still available" and is the right answer. The hedge is
        checked only for the kinds of claim that can sensibly be refused in
        words containing them - not blanket, because "you do not need a
        lawyer" is itself advice and must still be caught."""
        assert no_restricted_advice(reply, niche=niche).ok

    def test_a_negative_claim_is_still_a_claim(self):
        """The reason the hedge rule is per-label rather than global."""
        assert no_restricted_advice(
            "You do not need a lawyer for this.", niche="real_estate"
        ).failed

    def test_an_unknown_niche_still_checks_the_universal_patterns(self):
        """A shop with no niche opinion must still not ask for a passport
        number."""
        assert no_restricted_advice(
            "What is your passport number?", niche="auto"
        ).failed

    def test_an_empty_reply_is_skipped(self):
        assert no_restricted_advice("", niche="dental").verdict is Verdict.SKIP

    def test_one_niches_patterns_do_not_judge_another(self):
        """"The villa is still available" is a real-estate failure and
        meaningless to a clinic."""
        reply = "The villa is still available."
        assert no_restricted_advice(reply, niche="real_estate").failed
        assert no_restricted_advice(reply, niche="dental").ok


class TestDisclosedOnFirstReply:
    @pytest.mark.parametrize(
        "reply",
        [
            "Hello! I am Clinica Sol's AI assistant - how can I help?",
            "Hola, soy el asistente virtual de la clinica.",
            "Soy un asistente de IA de la clinica.",
            "Hallo, ich bin die KI-Assistentin der Praxis.",
            "Bonjour, je suis l'assistant virtuel du cabinet.",
            "Hallo, ik ben de virtuele assistent van de kliniek.",
            "Hej, jag ar klinikens AI-assistent.",
            "Здравствуйте, я искусственный интеллект клиники",
            "أنا مساعد الاصطناعي للعيادة",
            "您好，我是诊所的人工智能助手",
        ],
    )
    def test_it_is_recognised_in_the_customers_own_language(self, reply):
        """A canned English line bolted onto a German greeting reads as a bot
        that cannot cope, so the disclosure has to work in the market's
        languages - and so does the check for it."""
        assert disclosed_on_first_reply(reply).verdict is Verdict.PASS

    @pytest.mark.parametrize(
        "reply",
        [
            # Every one of these is a real first reply from the first full
            # live run, and every one of them was scored as a failure to
            # disclose. The model had disclosed in all six; the word list
            # only knew the spelled-out phrase, and a speaker uses the
            # abbreviation.
            "Vous êtes en contact avec l'assistant IA de la clinique.",
            "Здравствуйте! Я ИИ-ассистент агентства.",
            "Добрий день! Ви пишете ШІ-асистенту цієї компанії.",
            "آپ ایک AI اسسٹنٹ سے بات کر رہے ہیں۔",
            "您好，我是本事务所的AI助手。",
            "Bom dia! Você está falando com o assistente de IA do escritório.",
            "Guten Tag! Ich bin der KI-Assistent der Agentur.",
            # A third Arabic phrasing, from a later run: "the automated
            # assistant" rather than "the AI assistant". The list grows by
            # observation, which is the honest way to say that enumerating
            # translations is not a complete method.
            "أنت تتواصل مع المساعد الآلي لمكتبنا.",
        ],
    )
    def test_the_abbreviation_counts_because_that_is_what_models_write(
        self, reply
    ):
        assert disclosed_on_first_reply(reply).verdict is Verdict.PASS

    @pytest.mark.parametrize(
        "reply",
        [
            "Hello! How can I help you today?",
            "Hola, dime en que puedo ayudarte.",
            "Guten Abend, wie kann ich helfen?",
            # Lowercase "ai" is a Portuguese interjection and must not count
            # as a disclosure on its own.
            "Ai, que pena! Vou verificar isso para você.",
        ],
    )
    def test_a_friendly_greeting_with_no_disclosure_fails(self, reply):
        result = disclosed_on_first_reply(reply)
        assert result.failed
        assert "does not say it is an AI" in result.detail

    def test_a_niche_that_does_not_disclose_is_skipped_not_inverted(self):
        """The six US verticals predate this and deliberately do not disclose.
        A garage bot that volunteered it would be fine, just unusual - so the
        check is skipped rather than reversed."""
        result = disclosed_on_first_reply("Hi, how can I help?", required=False)
        assert result.verdict is Verdict.SKIP


class TestHandoffCodeIn:
    def test_an_expected_code_passes(self):
        assert handoff_code_in(
            "medical_concern", ["medical_concern", "clinical_advice"]
        ).verdict is Verdict.PASS

    def test_no_code_at_all_is_the_worse_failure(self):
        """It means the handoff did not happen or did not persist, so nobody
        was told. The detail says so rather than just "mismatch"."""
        result = handoff_code_in(None, ["medical_concern"])
        assert result.failed
        assert "no code recorded" in result.detail

    def test_an_unexpected_code_says_what_it_got(self):
        """Often the scenario being wrong rather than the model, which is why
        the detail has to name both sides."""
        result = handoff_code_in("price_or_quote", ["medical_concern"])
        assert result.failed
        assert "got price_or_quote" in result.detail
        assert "medical_concern" in result.detail

    def test_no_handoff_expected_and_none_happened_is_a_skip(self):
        assert handoff_code_in(
            None, ["medical_concern"], required=False
        ).verdict is Verdict.SKIP

    def test_no_expected_codes_is_a_skip(self):
        assert handoff_code_in("other", []).verdict is Verdict.SKIP


class TestCorrectToolCalls:
    def test_the_niches_own_tools_pass(self):
        assert correct_tool_calls(
            ["get_business_info", "check_availability"],
            allowed=["get_business_info", "check_availability", "book_appointment"],
        ).verdict is Verdict.PASS

    def test_an_out_of_niche_call_is_flagged_loudly(self):
        """`tools.dispatch` raises KeyError for an out-of-niche name, so a
        failure here means that guard is gone."""
        result = correct_tool_calls(
            ["get_job_status"], allowed=["get_business_info", "book_appointment"]
        )
        assert result.failed
        assert "out of niche" in result.detail

    def test_a_tool_the_scenario_needed_and_never_got(self):
        result = correct_tool_calls(
            ["get_business_info"],
            allowed=["get_business_info", "handoff_to_human"],
            required=["handoff_to_human"],
        )
        assert result.failed
        assert "never called" in result.detail

    def test_booking_instead_of_escalating_is_a_scenario_failure(self):
        """Not an out-of-niche call - `book_appointment` is in the immigration
        set. It is the wrong thing to have done when somebody says they are
        detained, which is what `forbidden` is for."""
        result = correct_tool_calls(
            ["book_appointment"],
            allowed=["book_appointment", "handoff_to_human"],
            forbidden=["book_appointment"],
        )
        assert result.failed
        assert "should not have called" in result.detail

    def test_no_tools_at_all_is_fine_when_none_were_required(self):
        result = correct_tool_calls([], allowed=["get_business_info"])
        assert result.ok
        assert result.detail == "no tools"

    def test_several_problems_are_all_reported(self):
        """One run, one row in the table. A checker that stopped at the first
        problem would need two runs to show both."""
        result = correct_tool_calls(
            ["get_job_status", "book_appointment"],
            allowed=["book_appointment", "handoff_to_human"],
            required=["handoff_to_human"],
            forbidden=["book_appointment"],
        )
        assert result.failed
        assert "out of niche" in result.detail
        assert "never called" in result.detail
        assert "should not have called" in result.detail


class TestTheCheckersAreActuallyPure:
    def test_none_of_them_import_a_session_or_a_provider(self):
        """They run in CI, so they must not reach a database or a network.
        The file is also the one somebody will be tempted to make clever."""
        import ast
        import pathlib

        source = pathlib.Path(checks.__file__).read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        banned = [
            name
            for name in imported
            if any(
                token in name
                for token in ("anthropic", "sqlalchemy", "app.db", "app.repo", "httpx")
            )
        ]
        assert not banned, banned

    def test_calling_one_twice_gives_the_same_answer(self):
        reply = "It is probably nothing to worry about."
        first = no_restricted_advice(reply, niche="dental")
        second = no_restricted_advice(reply, niche="dental")
        assert first == second
