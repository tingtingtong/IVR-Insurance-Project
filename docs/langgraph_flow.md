# LangGraph IVR flow

Generated from `build_graph()` by `python -m utils.graph_diagram`. Do not edit by hand.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	router(router)
	policy(policy)
	payment(payment)
	otp(otp)
	loan(loan)
	beneficiary(beneficiary)
	contact(contact)
	document(document)
	privacy(privacy)
	faq(faq)
	escalation(escalation)
	goodbye(goodbye)
	__end__([<p>__end__</p>]):::last
	__start__ --> router;
	beneficiary --> __end__;
	contact --> __end__;
	document --> __end__;
	escalation --> __end__;
	faq --> __end__;
	goodbye --> __end__;
	loan --> __end__;
	otp --> __end__;
	payment --> __end__;
	policy --> __end__;
	privacy --> __end__;
	router -.-> policy;
	router -.-> payment;
	router -.-> otp;
	router -.-> loan;
	router -.-> beneficiary;
	router -.-> contact;
	router -.-> document;
	router -.-> privacy;
	router -.-> faq;
	router -.-> escalation;
	router -.-> goodbye;
	router -.-> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

Dotted edges are the conditional routing in `_route_after_router`. Authentication is not a graph node: each service node calls `ensure_authenticated` (`core/graph/auth_guard.py`) and auth runs inside that turn.
