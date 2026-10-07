"""Text checks for the glitch-idea skill documents."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT / "glitch-idea" / "SKILL.md").read_text()
COMMANDS = (ROOT / "glitch-idea" / "references" / "commands.md").read_text()
STEPS = ["Capture", "Priorities", "Methods", "Discovery", "Exploration", "Visualize", "Assess", "Review"]


def section(text, heading):
    match = re.search(r"^### " + re.escape(heading) + r"\n(.*?)(?=^#{1,3} |\Z)", text, re.S | re.M)
    assert match, heading
    return match.group(1)


class SkillTextTests(unittest.TestCase):
    def test_apiv_framing_line(self):
        self.assertIn("This is Align, the A of APIV (Align · Plan · Implement · Verify); /glitch-plan is the P", SKILL)

    def test_eight_steps_in_order(self):
        line = next(l for l in SKILL.splitlines() if l.startswith("The browser flow has eight steps"))
        positions = [line.index(name) for name in STEPS]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("eight steps", COMMANDS)

    def test_session_room_paragraph(self):
        line = next(l for l in SKILL.splitlines() if l.startswith("**Session room.**"))
        body = " ".join(line.split())
        self.assertIn("A store keeps 8 sessions", body)
        self.assertRegex(body, r"reached Review frees itself.{0,80}unless its tab or agent is still open")
        self.assertRegex(body, r"`binding_capacity`.{0,200}ask which one to discard; never pick one yourself")
        self.assertRegex(body, r"`session-discard --binding <binding_id>`.{0,40}`session_in_use`.{0,160}`--confirm` only on their explicit yes")
        self.assertIn("session-open --resume", body)
        self.assertRegex(body, r"`session-open --resume <binding_id>` returns `binding_not_found`.{0,60}freed or discarded.{0,80}one line.{0,40}open a new session.{0,60}ideas are still in the store")
        self.assertRegex(" ".join(COMMANDS.split()), r"`binding_not_found`\. The same code from `session-open --resume binding_ID`.{0,120}freed.{0,80}discarded.{0,80}new session")
        self.assertIn("`sessions`", COMMANDS)
        self.assertIn("`session-discard --binding binding_ID", COMMANDS)

    def test_methods_recommends_only_when_asked_and_never_in_a_fill(self):
        methods = section(SKILL, "Methods")
        self.assertTrue(methods.strip())
        body = " ".join(methods.split())
        self.assertRegex(body, r"(?i)recommend\w* (only )?when the operator asks.{0,200}terminal only.{0,200}never in a fill")
        self.assertRegex(body, r"(?i)unasked.{0,80}nothing beyond the memory line")
        for status in ("found", "varied", "searched_no_preference", "unavailable", "error"):
            self.assertIn("`%s`" % status, methods)

    def test_stop_list_codes(self):
        text = section(SKILL, "Refusals, release and stops")
        stop = next(l for l in text.splitlines() if l.startswith("**Stop list.**"))
        for code in ("agent_unavailable", "owner_unavailable", "unsupported_idea_version"):
            self.assertIn(code, stop)
        self.assertIn("request_cancelled", text)
        self.assertIn("is not a stop", stop)
        hand = next(l for l in text.splitlines() if l.startswith("**Hand release.**"))
        self.assertIn("keep polling `events`", hand)

    def test_methods_memory_shape(self):
        methods = section(SKILL, "Methods")
        self.assertIn("preferred_method", methods)
        self.assertIn('"memory"', methods)
        self.assertNotIn("{status,sources,rationale}", SKILL)
        self.assertNotIn("{status,sources,rationale}", COMMANDS)
        self.assertIn("preferred_method", COMMANDS)

    def test_terminal_work_and_exploration_truth(self):
        flat = " ".join(SKILL.split())
        worked = re.search(r"Discovery and Exploration are worked out WITH the operator[^.]*\.", flat)
        self.assertIsNotNone(worked)
        self.assertNotIn("method reason", worked.group(0))
        self.assertNotIn("selected method", SKILL)
        self.assertNotIn("can remain null", SKILL)
        self.assertIn("Capture and Priorities are the operator's alone", flat)
        self.assertIn("Visualize and Review happen in the page", flat)

    def test_discovery_questions_and_challenge(self):
        discovery = section(SKILL, "Discovery")
        questions = ["What is the problem?", "Who does it serve?", "How is it handled today?",
                     "What evidence says it is needed?", "What would kill it?"]
        positions = [discovery.index(q) for q in questions]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("real challenge", discovery)
        self.assertIn("Dig deeper", discovery)

    def test_exploration_order_and_stop(self):
        text = section(SKILL, "Exploration")
        order = ["desired result", "alternatives", "uncertainty and risk", "scope and why", "next slice"]
        positions = [text.index(o) for o in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("/glitch-plan's job", text)

    def test_exploration_names_explicit_empty_lists(self):
        flat = " ".join(section(SKILL, "Exploration").split())
        self.assertIn("`assumptions`", flat)
        self.assertIn("`learning`", flat)
        self.assertIn("an empty list (`[]`), never null", flat)
        self.assertIn("never omit", flat)

    def test_assess_names_the_seven_keys(self):
        assess = section(SKILL, "Assess")
        for key in ("method", "version", "inputs", "basis", "assumptions", "confidence", "provenance"):
            self.assertIn("`%s`" % key, assess)

    def test_no_shape_verb_left(self):
        for name, text in (("SKILL.md", SKILL), ("commands.md", COMMANDS)):
            self.assertIsNone(re.search(r"(?i)\bshape\b", text), name)
        self.assertIn("`exploration`", COMMANDS)

    def test_no_cd_chain_in_examples(self):
        for text in (SKILL, COMMANDS):
            self.assertIsNone(re.search(r"\bcd [^\n]*&&", text))

    def test_launch_paragraph_names_fragment_pairing(self):
        launch = next(p for p in SKILL.split("\n\n") if p.startswith("**Launch.**"))
        flat = " ".join(launch.split())
        self.assertIn("#pair=", flat)
        self.assertIn("fallback_line", flat)
        self.assertRegex(flat, r"only (if|when) the operator (says|reports) the tab did not open paired")
        self.assertIn("session-open --resume", flat)
        self.assertNotIn("Tell the operator only the fallback code line", flat)


class VisualizeTextTests(unittest.TestCase):
    def test_visualize_section_documents_three_choices_and_prototype(self):
        viz = section(SKILL, "Visualize")
        for label in ("Visualize in Claude Design and import it back", "Prototype Here", "Skip visualization"):
            self.assertIn(label, viz)
        self.assertIn("prototype_skill", viz)
        self.assertIn("https://github.com/mattpocock/skills", viz)
        self.assertIn("prototype-serve", viz)
        self.assertRegex(" ".join(viz.split()), r"never installs")

    def test_visualize_names_asset_verb_and_background_serve(self):
        flat = " ".join(section(SKILL, "Visualize").split())
        self.assertRegex(flat, r"idea\.py asset --session")
        self.assertRegex(flat, r"prototype-serve.{0,120}runs until the session ends.{0,60}background")
        flat = " ".join(COMMANDS.split())
        self.assertIn("asset --session", flat)
        self.assertNotIn("see `idea.py --help`", flat)

    def test_prototype_order_is_upload_fill_then_reply_last(self):
        order = r"upload both files.{0,200}then `?fill`?.{0,200}reply `?prototype_skill: \"available\"`? last.{0,200}unavailable\W{1,3} at once"
        self.assertRegex(" ".join(section(SKILL, "Visualize").split()), order)
        self.assertRegex(" ".join(COMMANDS.split()), order)
        self.assertNotRegex(" ".join(section(SKILL, "Visualize").split()), r"Your reply carries")

    def test_prototype_reply_waits_for_prototype_ready(self):
        rule = r"then `?fill`?.{0,200}look at the page.{0,200}reply `?prototype_skill: \"available\"`? last.{0,200}confirms.{0,120}Prototype ready.{0,600}request_cancelled.{0,40}request_closed.{0,200}(says|say) nothing more"
        self.assertRegex(" ".join(section(SKILL, "Visualize").split()), rule)
        self.assertRegex(" ".join(COMMANDS.split()), rule)

    def test_commands_document_prototype_serve_and_asset_door(self):
        self.assertIn("prototype-serve", COMMANDS)
        self.assertIn("/agent/v1/asset", COMMANDS)

    def test_not_applicable_is_gone(self):
        self.assertNotIn("not-applicable", SKILL)
        self.assertNotIn("not-applicable", COMMANDS)


def flat(text):
    return " ".join(text.split())


class AgentProtocolTextTests(unittest.TestCase):
    """One protocol: fill for Method, Discovery, Exploration and Assess; respond only for visual_brief."""

    def test_one_protocol_line_fill_only_respond_for_visual_brief(self):
        for text in (SKILL, COMMANDS):
            self.assertRegex(flat(text), r"Method, Discovery, Exploration and Assess are answered with `fill` only.{0,200}`respond` is for `visual_brief` only")

    def test_method_memory_must_match_what_this_agent_filled(self):
        text = flat(SKILL)
        self.assertRegex(text, r"Method memory with found/no-preference must match the memory this connected agent filled into the open request \(or a recorded reply\)")
        self.assertNotIn("must match verified current-generation Memory or Method evidence", text)

    def test_no_respond_for_memory_or_assessment(self):
        self.assertNotRegex(flat(SKILL), r"respond.{0,40}carries only a memory claim")
        self.assertNotRegex(flat(SKILL), r"Respond to that request once")
        self.assertNotIn("For a Memory event", SKILL)
        self.assertNotRegex(flat(SKILL), r"(?i)reply with `?respond`? .{0,40}memory")

    def test_optional_exploration_fields_are_omitted_in_a_fill_and_null_in_the_file(self):
        rule = r"(?i)never send `null` as a fill field value.{0,200}leave out `investment` and `experiment`"
        self.assertRegex(flat(SKILL), rule)
        self.assertRegex(flat(COMMANDS), rule)
        self.assertRegex(flat(COMMANDS), r"next-slice file.{0,1500}`investment` and `experiment` as `null`")

    def test_five_empty_waits_have_a_next_action(self):
        rule = r"five consecutive empty waits.{0,200}tell the operator in one line you are pausing.{0,200}resume with `events`"
        self.assertRegex(flat(SKILL), rule)

    def test_review_sends_no_request_and_agent_unavailable_ends_the_run(self):
        rule = r"Review sends no request.{0,300}`agent_unavailable` .{0,40}means the run is over, not an error"
        self.assertRegex(flat(SKILL), rule)
        self.assertRegex(flat(COMMANDS), rule)

    def test_exploration_names_real_method_input_keys(self):
        text = flat(section(SKILL, "Exploration"))
        for key in ("question", "evidence", "success_criterion", "stop_rule", "cap", "unit", "boundary"):
            self.assertIn(key, text)
        self.assertNotIn("success and stop", text)

    def test_null_rule_names_every_documented_nested_null(self):
        for text in (SKILL, COMMANDS):
            body = flat(text)
            self.assertRegex(body, r"(?i)no top-level fill field may be null.{0,200}nested nulls.{0,120}`preferred_method`.{0,80}sketch item")
            self.assertNotRegex(body, r"(the one null allowed|The only null|the only null) is `preferred_method`")

    def test_review_tells_normal_end_from_failure(self):
        for text in (SKILL, COMMANDS):
            body = flat(text)
            self.assertRegex(body, r"Review sends no request.{0,900}Assess is accepted.{0,300}read-only `show`.{0,200}current_step`.{0,60}`review`.{0,300}stop list")

    def test_background_events_wait_keeps_the_lease_alive(self):
        for text in (SKILL, COMMANDS):
            body = flat(text)
            self.assertRegex(body, r"(?i)right after launch, start one background shell loop that runs `events` again by itself.{0,200}lease never lapses between turns.{0,200}(exits|wak).{0,120}only when a request is delivered or a stop code")
            self.assertRegex(body, r"(?i)never re-run waits.{0,80}turn by turn.{0,120}every 25 seconds")
        self.assertRegex(flat(SKILL), r"(?i)empty waits never reset the idle clock, so a forgotten session still pauses after 600 seconds")

    def test_terminal_fills_reset_the_idle_clock(self):
        for text in (SKILL, COMMANDS):
            body = flat(text)
            self.assertRegex(body, r"(?i)saves, pairing.{0,80}still here.{0,40}pings.{0,80}(and )?the connected agent's accepted fills.{0,60}reset")
            self.assertRegex(body, r"(?i)empty waits do not")

    def test_prototype_skill_is_identified_by_origin(self):
        body = flat(section(SKILL, "Visualize"))
        self.assertRegex(body, r"identify it by its origin.{0,200}mattpocock/skills.{0,80}skills/engineering/prototype.{0,40}MIT.{0,120}under any installed name.{0,120}different skill.{0,40}called `prototype` is not it")
        self.assertRegex(flat(COMMANDS), r"identified by its origin.{0,200}under any installed name")

    def test_visualize_skip_sends_no_request(self):
        body = flat(section(SKILL, "Visualize"))
        self.assertRegex(body, r"(?i)skip.{0,80}Claude Design import.{0,60}sends the terminal no request.{0,200}next request.{0,200}read your events before telling the operator anything about Visualize")

class MovedIdeaTextTests(unittest.TestCase):
    def test_both_workspace_flags_and_deliver_are_documented(self):
        for text in (SKILL, COMMANDS):
            body = flat(text)
            self.assertIn("--workspace-name", body)
            self.assertIn("--workspace-path", body)
            self.assertIn("deliver", body)
            self.assertIn("`idea_moved`", body)
        self.assertRegex(flat(SKILL), r"both `--workspace-name NAME` and `--workspace-path /absolute/folder`.{0,40}both or neither")
        self.assertRegex(flat(COMMANDS), r"given together or not at all")

    def test_moved_ideas_are_read_only(self):
        for text in (SKILL, COMMANDS):
            body = flat(text)
            self.assertRegex(body, r"(?i)moved or delivered idea.{0,60}(read-only|answers `show` and `list`)")
            self.assertIn("500 characters", body)
        self.assertRegex(flat(SKILL), r"glitch-idea will refuse further edits")
        self.assertRegex(flat(COMMANDS), r"There is no un-deliver")


if __name__ == "__main__":
    unittest.main()
