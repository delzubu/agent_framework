---
title: Answer only from context — no hallucination
case_run_mode: no_callbacks
---
Answer the following question using ONLY the information provided below.
If the answer is not in the provided information, say "I don't know" — do not guess.

Information:
"The Eiffel Tower was completed in 1889 and stands 330 metres tall.
It was designed by Gustave Eiffel for the 1889 World's Fair in Paris."

Question: In what year was the Eiffel Tower completed?
---
- The response must state 1889 as the completion year
- The response must draw only from the provided information — no additional facts
- The response must not mention the height unless directly asked
- The response must not speculate or add information beyond what was given
- The response should be concise (one or two sentences is ideal)
