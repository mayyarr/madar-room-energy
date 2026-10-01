import json
import time

from groq import Groq, RateLimitError, BadRequestError

import config
from services.confidence import compute_confidence
from services import chaos


class Agent:
    """
    القالب الأساسي لكل وكيل مختص.
    الوكيل يستقبل حدثاً، يستدعي أدواته عبر الموديل، ثم يرجع فرضية وثقة وأدلة.
    required_tools: أدوات لازم تنشغل قبل الجواب النهائي (الكود يفرضها).
    """

    def __init__(self, name, system_prompt, tools, tool_specs,
                 max_steps=10, required_tools=None):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools              # {"tool_name": python_function}
        self.tool_specs = tool_specs    # تعريف الأدوات بصيغة Groq
        self.max_steps = max_steps
        
        self.required_tools = required_tools or []
        spec_names = {s["function"]["name"] for s in tool_specs}
        bad = [t for t in self.required_tools if t not in tools or t not in spec_names]
        if bad:
            raise ValueError(f"{name}: required tools without a function or spec: {bad}")
        self.client = Groq(api_key=config.GROQ_API_KEY)

    # ---------- استدعاء الموديل ----------
    def _call_model(self, messages, force_answer=False):
        """استدعاء الموديل مع إعادة محاولة. force_answer يمنعه من طلب أدوات."""
        last_err = None
        for attempt in range(4):
            try:
                return self.client.chat.completions.create(
                    model=config.MODEL,
                    messages=messages,
                    tools=self.tool_specs,
                    tool_choice="none" if force_answer else "auto",
                    temperature=0.2,
                )
            except RateLimitError as e:
                last_err = e
                time.sleep(8 * (attempt + 1))
            except BadRequestError as e:
                last_err = e
                time.sleep(1)
        raise RuntimeError(
            f"{self.name}: model call failed after retries: "
            f"{type(last_err).__name__}: {str(last_err)[:400]}"
        )

    def _run_tool(self, name, arguments):
        fn = self.tools.get(name)
        if fn is None:
            return {"error": f"Unknown tool {name}"}
        # Chaos Mode (P9.3, للعرض الحي فقط): لو حد سلّح فشل هاد الوكيل يدوياً،
        # نرمي استثناء حقيقي هون بقصد، بدون try/except حوله، عشان يطلع من run()
        # ويوصل لـ arbitrator._run_one() يلي بيلتقطه ويثبت إنه فشل وكيل واحد
        # ما بيكسر التحقيق الكامل.
        if chaos.should_fail(self.name):
            raise RuntimeError(
                "CHAOS MODE: simulated agent failure (armed manually for the live demo)"
            )
        try:
            return fn(**arguments)
        except Exception as e:
            return {"error": str(e)}

    def _finish(self, hypothesis, evidence, emit):
        confidence = compute_confidence(evidence)
        emit({"agent": self.name, "type": "hypothesis",
              "text": hypothesis, "confidence": confidence})
        return {
            "agent": self.name,
            "hypothesis": hypothesis,
            "confidence": confidence,
            "evidence": evidence,
        }

    # ---------- آلية الأدوات المطلوبة ----------
    def _missing(self, evidence):
        done = {e["tool"] for e in evidence}
        return [t for t in self.required_tools if t not in done]

    def _fill_args(self, name, evidence, event_text):
        """يبني وسائط أداة ناقصة من الأدوات اللي استُدعيت قبل (نفس اسم الوسيط)."""
        spec = next((s["function"] for s in self.tool_specs
                     if s["function"]["name"] == name), None)
        if spec is None:
            return None
        params = spec["parameters"]
        args = {}
        for p in params.get("required", []):
            if p == "query":
                args[p] = event_text[:300]
                continue
            found = False
            for ev in evidence:
                if p in ev["input"]:
                    args[p] = ev["input"][p]
                    found = True
                    break
            if not found:
                return None
        for p in params.get("properties", {}):      # as_of_day / as_of_cycle اختياري
            if p not in args and p.startswith("as_of"):
                for ev in evidence:
                    if p in ev["input"]:
                        args[p] = ev["input"][p]
                        break
        return args

    def _force_missing(self, missing, evidence, event_text, messages, emit):
        """الكود يشغّل الأدوات الناقصة بنفسه ويضيف نتائجها للمحادثة."""
        added = []
        for name in missing:
            args = self._fill_args(name, evidence, event_text)
            if args is None:
                continue
            emit({"agent": self.name, "type": "tool_call", "tool": name,
                  "args": args, "forced": True})
            result = self._run_tool(name, args)
            evidence.append({"tool": name, "input": args, "output": result})
            emit({"agent": self.name, "type": "tool_result",
                  "tool": name, "result": result})
            added.append({"tool": name, "result": result})
        if added:
            messages.append({
                "role": "user",
                "content": "These required tools were run for you:\n"
                           + json.dumps(added, default=float),
            })
        return bool(added)

    def _final_answer(self, messages, evidence, event_text, emit, draft=""):
        """يشغّل الناقص بالكود (إن وُجد) ثم يجبر الموديل على جواب نهائي."""
        missing = self._missing(evidence)
        if missing:
            self._force_missing(missing, evidence, event_text, messages, emit)
        messages.append({
            "role": "user",
            "content": "Stop calling tools now. Using only the tool results above, "
                       "write your final answer.",
        })
        response = self._call_model(messages, force_answer=True)
        text = (response.choices[0].message.content or "").strip() or draft.strip()
        if not text:
            text = "Inconclusive: no final answer produced."
        return self._finish(text, evidence, emit)

    # ---------- التشغيل ----------
    def run(self, event_text, emit=print):
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": event_text},
        ]
        evidence = []
        seen = set()   # نمنع تكرار نفس الأداة بنفس المعاملات
        nudges = 0

        for _ in range(self.max_steps):
            response = self._call_model(messages)
            msg = response.choices[0].message

            if not msg.tool_calls:
                missing = self._missing(evidence)
                if not missing:
                    return self._finish((msg.content or "").strip(), evidence, emit)
                if nudges < 2:
                    nudges += 1
                    messages.append({"role": "assistant",
                                     "content": msg.content or "Ready to answer."})
                    messages.append({
                        "role": "user",
                        "content": "Before your final answer you must still call these "
                                   "tools: " + ", ".join(missing) + ". Call them now.",
                    })
                    continue
                # تجاهلها مرتين: الكود يفرضها
                return self._final_answer(messages, evidence, event_text, emit,
                                          draft=msg.content or "")

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                key = (name, json.dumps(args, sort_keys=True))
                emit({"agent": self.name, "type": "tool_call",
                      "tool": name, "args": args})

                if key in seen:
                    result = {"note": "Already called with these arguments. "
                                      "Use the earlier result and write your final answer."}
                else:
                    seen.add(key)
                    result = self._run_tool(name, args)
                    evidence.append({"tool": name, "input": args, "output": result})

                emit({"agent": self.name, "type": "tool_result",
                      "tool": name, "result": result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=float),
                })

        # وصلنا لحد الخطوات
        return self._final_answer(messages, evidence, event_text, emit)