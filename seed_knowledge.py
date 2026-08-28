"""
seed_knowledge.py — Populate the insuranceCompany RAG knowledge base (pgvector).

Usage:
    python seed_knowledge.py              # seed all categories
    python seed_knowledge.py --reset      # drop existing collection first, then seed
    python seed_knowledge.py --dry-run    # print chunks without writing to DB

Requires PostgreSQL + pgvector running (docker-compose up postgres).
"""
import sys
import asyncio
import argparse

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from langchain_core.documents import Document


# ── Knowledge chunks ──────────────────────────────────────────────────────────
# Each tuple: (content, metadata_dict)
# metadata keys: category, subcategory, keywords

KNOWLEDGE_BASE: list[tuple[str, dict]] = [

    # ════════════════════════════════════════════════════════════════════
    # 1. POLICY TYPES & PRODUCTS
    # ════════════════════════════════════════════════════════════════════
    (
        "Whole Life insurance provides permanent lifetime coverage with a guaranteed death benefit "
        "paid to your named beneficiary upon the insured's death. Unlike term life, it does not "
        "expire as long as premiums are paid. It also builds cash value over time at a guaranteed "
        "rate, which you can borrow against through a policy loan.",
        {"category": "policy_types", "subcategory": "whole_life",
         "keywords": "whole life permanent coverage cash value death benefit"},
    ),
    (
        "Term Life insurance provides coverage for a fixed period, typically 10, 20, or 30 years. "
        "It pays a death benefit only if the insured dies during the term. Term life has no cash "
        "value and premiums are generally lower than whole life for the same coverage amount. "
        "If you outlive the term, the coverage ends with no payout.",
        {"category": "policy_types", "subcategory": "term_life",
         "keywords": "term life temporary coverage fixed period no cash value"},
    ),
    (
        "Medicare Supplement insurance, also called Medigap, helps pay costs not covered by "
        "Original Medicare such as copayments, coinsurance, and deductibles. It does not provide "
        "a death benefit and is not a life insurance policy. Premiums depend on the plan letter "
        "from Plan A through Plan N and your age and location.",
        {"category": "policy_types", "subcategory": "medicare_supplement",
         "keywords": "medicare supplement medigap copay deductible plan A N"},
    ),
    (
        "Universal Life insurance is a flexible permanent life insurance policy. You can adjust "
        "your premium payments and death benefit within certain limits. It builds cash value based "
        "on current interest rates. If the cash value is depleted due to low payments or poor "
        "performance, the policy may lapse.",
        {"category": "policy_types", "subcategory": "universal_life",
         "keywords": "universal life flexible premium adjustable death benefit interest"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 2. POLICY STATUS
    # ════════════════════════════════════════════════════════════════════
    (
        "A policy in Active status means your coverage is current and in force. Premiums are "
        "being paid on time and no lapse has occurred. You are fully protected under the terms "
        "of your policy.",
        {"category": "policy_status", "subcategory": "active",
         "keywords": "active status in force current coverage"},
    ),
    (
        "A Lapsed policy means coverage has ended because premiums were not paid within the "
        "grace period. A lapsed whole life policy may be reinstated within 3 years if you pay "
        "all overdue premiums plus interest and provide proof of insurability. A lapsed term "
        "policy generally cannot be reinstated after the grace period.",
        {"category": "policy_status", "subcategory": "lapsed",
         "keywords": "lapsed expired reinstated overdue premiums grace period"},
    ),
    (
        "A Paid-Up policy means you have paid all required premiums and the policy is fully "
        "funded. No further premium payments are required. The death benefit remains in force "
        "for the rest of your life at no additional cost.",
        {"category": "policy_status", "subcategory": "paid_up",
         "keywords": "paid up no more premiums fully funded"},
    ),
    (
        "A Surrendered policy means the policyholder has cancelled the policy and received "
        "the cash surrender value. Once surrendered, the death benefit is permanently forfeited. "
        "Tax implications may apply to any gains received above the premiums paid.",
        {"category": "policy_status", "subcategory": "surrendered",
         "keywords": "surrendered cancelled cash surrender value tax"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 3. PREMIUMS & BILLING
    # ════════════════════════════════════════════════════════════════════
    (
        "Your premium is the amount you pay to keep your life insurance policy active. Premiums "
        "can be billed monthly, quarterly, semi-annually, or annually. Paying annually or "
        "semi-annually typically saves you a small amount compared to monthly billing. Your "
        "premium amount is fixed at the time of issue for whole life and term policies.",
        {"category": "premiums", "subcategory": "billing_modes",
         "keywords": "premium payment monthly quarterly annual billing mode"},
    ),
    (
        "The Paid-To-Date field shows the date through which your policy premiums are paid. "
        "If your paid-to-date is in the future your policy is current. If it is today or in "
        "the past a payment may be due. Your policy remains in force during the 30-day grace "
        "period after a missed payment.",
        {"category": "premiums", "subcategory": "paid_to_date",
         "keywords": "paid to date current premium due date grace period"},
    ),
    (
        "Autopay automatically deducts your premium from your bank account or credit card on "
        "your billing date each month. To enroll in autopay you will need your bank routing "
        "number and account number or your credit or debit card information. Autopay helps "
        "avoid missed payments and potential policy lapse.",
        {"category": "premiums", "subcategory": "autopay",
         "keywords": "autopay automatic payment bank account debit enroll billing"},
    ),
    (
        "If you miss a premium payment your policy enters a 30-day grace period. During the "
        "grace period your coverage remains fully active. If payment is not received within "
        "30 days of the due date the policy may lapse. For whole life policies with sufficient "
        "cash value an automatic premium loan may be used to keep the policy in force.",
        {"category": "premiums", "subcategory": "grace_period",
         "keywords": "missed payment grace period 30 days lapse automatic premium loan"},
    ),
    (
        "Online and phone payments post to your account within 24 to 48 hours. Payments made "
        "by check through the mail take 7 to 10 business days to post. Your policy will show "
        "as current once the payment has been applied.",
        {"category": "premiums", "subcategory": "payment_posting",
         "keywords": "payment posting 24 48 hours 7 10 business days online phone mail check"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 4. PAYMENTS
    # ════════════════════════════════════════════════════════════════════
    (
        "You can make a one-time payment over the phone using a credit card, debit card, or "
        "bank account ACH. We accept Visa, Mastercard, and Discover. American Express is not "
        "accepted. Prepaid cards without a cardholder name are not accepted.",
        {"category": "payments", "subcategory": "payment_methods",
         "keywords": "payment card credit debit bank ACH visa mastercard discover prepaid"},
    ),
    (
        "To make a bank account ACH payment you will need your bank routing number, which is "
        "the 9-digit number at the bottom left of your check, and your account number. By "
        "providing these details you authorize insuranceCompany to initiate a one-time electronic "
        "debit from your account. This authorization is for the current payment only.",
        {"category": "payments", "subcategory": "ach_payment",
         "keywords": "ACH bank account routing number authorization debit one-time"},
    ),
    (
        "To make a card payment you will need your 16-digit card number, the card expiry date "
        "in month and year format, and the 3 or 4 digit security code on the back of your card. "
        "Card payments post within 24 to 48 hours.",
        {"category": "payments", "subcategory": "card_payment",
         "keywords": "card payment 16 digit expiry CVV security code credit debit"},
    ),
    (
        "Prepaid cards cannot be accepted for premium payments if the card does not have a "
        "cardholder name printed on it. This is a payment processing restriction. We recommend "
        "using a bank account or a standard credit or debit card instead.",
        {"category": "payments", "subcategory": "prepaid_card_restriction",
         "keywords": "prepaid card declined no name restriction"},
    ),
    (
        "Your payment history shows the last 3 premium payments applied to your policy including "
        "the amount paid and the date each payment was posted. If a payment is missing allow "
        "up to 10 business days for mailed checks to appear.",
        {"category": "payments", "subcategory": "payment_history",
         "keywords": "payment history last transactions posted date amount"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 5. CASH VALUE & POLICY LOANS
    # ════════════════════════════════════════════════════════════════════
    (
        "Cash value is the savings component of a permanent life insurance policy such as whole "
        "life or universal life. It grows tax-deferred over time. You can access cash value "
        "through a policy loan or a full or partial surrender of the policy.",
        {"category": "cash_value", "subcategory": "what_is_cash_value",
         "keywords": "cash value savings permanent policy tax deferred accumulation"},
    ),
    (
        "A policy loan allows you to borrow against the cash value of your whole life or "
        "universal life policy. There is no credit check or approval process. The loan accrues "
        "interest typically at a rate of 5 to 8 percent per year. You are not required to repay "
        "the loan but any outstanding loan balance plus interest will be deducted from the "
        "death benefit paid to your beneficiary.",
        {"category": "policy_loans", "subcategory": "loan_overview",
         "keywords": "policy loan borrow cash value interest rate death benefit deduction no credit check"},
    ),
    (
        "Your policy loan balance is the total amount borrowed from your policy cash value. "
        "Accrued interest is interest that has accumulated on the outstanding loan balance but "
        "has not yet been paid. The payoff amount is the loan balance plus all accrued interest "
        "needed to fully repay the loan today.",
        {"category": "policy_loans", "subcategory": "loan_balance",
         "keywords": "loan balance accrued interest payoff amount outstanding"},
    ),
    (
        "If you do not repay your policy loan and the loan balance exceeds the cash value of "
        "your policy the policy will lapse. You will receive a tax form 1099 if the lapsed "
        "amount exceeds the premiums you paid in. Contact us to discuss repayment options "
        "before your cash value is fully depleted.",
        {"category": "policy_loans", "subcategory": "loan_lapse_risk",
         "keywords": "loan lapse cash value depleted 1099 tax repayment"},
    ),
    (
        "A paid-up addition is a small amount of paid-up whole life insurance purchased with "
        "policy dividends. Paid-up additions increase your total death benefit and cash value "
        "over time without requiring additional premium payments.",
        {"category": "policy_loans", "subcategory": "paid_up_addition",
         "keywords": "paid up addition dividend death benefit cash value increase"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 6. BENEFICIARIES
    # ════════════════════════════════════════════════════════════════════
    (
        "A beneficiary is the person or entity you name to receive the death benefit when the "
        "insured passes away. You can name one or more primary beneficiaries and contingent "
        "or backup beneficiaries. The death benefit is split according to the percentage "
        "assigned to each beneficiary.",
        {"category": "beneficiaries", "subcategory": "what_is_beneficiary",
         "keywords": "beneficiary death benefit primary contingent percentage named"},
    ),
    (
        "To change your beneficiary the policy owner must submit a written request. We cannot "
        "accept beneficiary changes over the phone. A beneficiary change form will be mailed "
        "to the address on file for you to complete, sign, and return. The change takes effect "
        "when the completed form is received and processed.",
        {"category": "beneficiaries", "subcategory": "change_beneficiary",
         "keywords": "change beneficiary written request form mailed signed policy owner"},
    ),
    (
        "A contingent beneficiary receives the death benefit only if all primary beneficiaries "
        "have predeceased the insured. If no contingent beneficiary is named and all primary "
        "beneficiaries have passed the death benefit is paid to the insured estate.",
        {"category": "beneficiaries", "subcategory": "contingent_beneficiary",
         "keywords": "contingent beneficiary backup secondary predeceased estate"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 7. DOCUMENTS & DELIVERY
    # ════════════════════════════════════════════════════════════════════
    (
        "Documents such as policy statements, annual reports, and premium notices can be mailed "
        "or faxed to the address on file. We are unable to deliver policy documents by email "
        "per our compliance and security policy. If you need to update your mailing address "
        "please contact us and we will update your record.",
        {"category": "documents", "subcategory": "delivery_policy",
         "keywords": "documents mail fax no email compliance security address"},
    ),
    (
        "To request a copy of your policy, a beneficiary designation form, or a policy "
        "illustration, contact us and a copy will be mailed to the address on file within "
        "7 to 10 business days. Rush requests are not available.",
        {"category": "documents", "subcategory": "policy_copy",
         "keywords": "policy copy request mailed 7 10 business days illustration form"},
    ),
    (
        "Annual policy statements are mailed each year and show your current death benefit, "
        "cash value, any outstanding loan balance, and premium paid in the prior year. If you "
        "did not receive your annual statement we can mail a replacement to your address on file.",
        {"category": "documents", "subcategory": "annual_statement",
         "keywords": "annual statement death benefit cash value loan premium mailed"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 8. CONTACT & ADDRESS CHANGES
    # ════════════════════════════════════════════════════════════════════
    (
        "To update your mailing address or phone number the policy owner must verify their "
        "identity first. Address changes require written confirmation sent to your previous "
        "address as a security measure. Phone number updates can be processed over the phone "
        "after authentication.",
        {"category": "contact_changes", "subcategory": "address_phone_update",
         "keywords": "update address phone number written confirmation security"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 9. OWNER CHANGES
    # ════════════════════════════════════════════════════════════════════
    (
        "A policy owner change transfers ownership of the policy to another person. The current "
        "owner must submit a written request with their signature. Owner changes cannot be "
        "processed over the phone. A change of ownership form will be mailed to the address "
        "on file and must be returned signed by the current owner.",
        {"category": "owner_changes", "subcategory": "owner_change_process",
         "keywords": "owner change written signed form transfer ownership"},
    ),
    (
        "The policy owner has all rights to the policy including the ability to change "
        "beneficiaries, take loans, and surrender the policy. The insured is the person whose "
        "life is insured. The owner and the insured can be the same person or different people.",
        {"category": "owner_changes", "subcategory": "owner_vs_insured",
         "keywords": "owner insured rights beneficiary loan surrender difference"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 10. PRIVACY / GLBA
    # ════════════════════════════════════════════════════════════════════
    (
        "Under the Gramm-Leach-Bliley Act you have the right to opt out of having your personal "
        "financial information shared with affiliated companies and non-affiliated third parties. "
        "To opt out you can contact us by phone and we will process your request. Your opt-out "
        "will remain in effect until you change your election.",
        {"category": "privacy", "subcategory": "glba_opt_out",
         "keywords": "GLBA privacy opt out affiliated non-affiliated third party sharing"},
    ),
    (
        "insuranceCompany collects personal information such as your name, address, date of birth, "
        "Social Security number, policy details, and payment information. This information is "
        "used to administer your policy, process payments, and communicate with you about your "
        "account. We do not sell your personal information to unaffiliated third parties for "
        "their own marketing purposes.",
        {"category": "privacy", "subcategory": "data_collection",
         "keywords": "personal information collected SSN name address policy data not sold"},
    ),
    (
        "You may opt out of sharing your personal information with non-affiliated companies for "
        "marketing purposes only. Opting out does not affect our ability to share information "
        "required to process your policy, handle claims, or comply with legal requirements.",
        {"category": "privacy", "subcategory": "opt_out_scope",
         "keywords": "opt out non-affiliated marketing policy processing legal compliance"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 11. CLAIMS
    # ════════════════════════════════════════════════════════════════════
    (
        "To file a life insurance claim the beneficiary must submit a completed claim form along "
        "with a certified copy of the death certificate. Claim forms can be mailed to the address "
        "on file or downloaded from our website. Claims are typically processed within 10 to 30 "
        "business days once all required documents are received.",
        {"category": "claims", "subcategory": "claim_process",
         "keywords": "claim death benefit death certificate form beneficiary 10 30 business days"},
    ),
    (
        "The IVR system cannot process insurance claims. If you are calling to report a death "
        "or file a claim please ask to be transferred to a live agent who can assist you with "
        "the claims process.",
        {"category": "claims", "subcategory": "claim_ivr_limitation",
         "keywords": "claim cannot process phone agent transfer death report"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 12. DIVIDENDS
    # ════════════════════════════════════════════════════════════════════
    (
        "Dividends are a return of a portion of your premium when the company performs better "
        "than expected. Not all policies pay dividends. Only participating whole life policies "
        "are dividend-eligible. Dividends are not guaranteed and can vary year to year.",
        {"category": "dividends", "subcategory": "what_are_dividends",
         "keywords": "dividends participating whole life return premium not guaranteed"},
    ),
    (
        "Dividends can be applied in several ways: as a cash payment, to reduce your next "
        "premium, to purchase paid-up additions for more coverage, or to accumulate at interest "
        "within the policy. You can change your dividend option by contacting us.",
        {"category": "dividends", "subcategory": "dividend_options",
         "keywords": "dividend options cash reduce premium paid-up addition accumulate interest"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 13. GENERAL & IVR NAVIGATION
    # ════════════════════════════════════════════════════════════════════
    (
        "insuranceCompany, part of CNO Financial Group, is one of the largest life insurance "
        "organizations in the United States focused on the middle-income market. Our brands "
        "include Bankers Life, Washington National, and Colonial Penn Life Insurance Company. "
        "We have been serving customers since 1935.",
        {"category": "company_info", "subcategory": "about_cno",
         "keywords": "CNO financial group bankers life colonial penn washington national history 1935"},
    ),
    (
        "To speak to a live agent say agent, representative, transfer, or speak to someone. "
        "You can also press zero at any time to be transferred to a live customer service "
        "representative. Live agent hours are Monday through Friday 8 AM to 8 PM Eastern Time.",
        {"category": "general", "subcategory": "transfer_to_agent",
         "keywords": "live agent representative transfer speak human zero Monday Friday 8am 8pm"},
    ),
    (
        "To authenticate and access your account you will be asked to provide two pieces of "
        "identifying information. These may include your phone number, policy number, date of "
        "birth, or the insured full name. This is to protect the security of your account.",
        {"category": "general", "subcategory": "authentication_info",
         "keywords": "authenticate identity verification phone policy number DOB name security"},
    ),
    (
        "Coverage changes, policy cancellations, surrenders, claims, and legal or medical advice "
        "cannot be handled through the IVR. For these matters you will be transferred to a "
        "licensed customer service representative who can assist you.",
        {"category": "general", "subcategory": "ivr_limitations",
         "keywords": "cannot coverage change cancel surrender claim legal medical agent transfer"},
    ),

    # ════════════════════════════════════════════════════════════════════
    # 14. COMPLIANCE SCRIPTS
    # ════════════════════════════════════════════════════════════════════
    (
        "ACH Authorization Script: By providing your bank routing number and account number "
        "you authorize insuranceCompany Financial Services to initiate a one-time electronic debit "
        "from your account for the amount stated. This authorization is for today's payment only "
        "and does not enroll you in automatic recurring payments.",
        {"category": "compliance_scripts", "subcategory": "ach_authorization",
         "keywords": "ACH authorization routing account debit one-time electronic not recurring"},
    ),
    (
        "Privacy Opt-Out Script: You have the right to limit the sharing of your personal "
        "financial information with our affiliated and non-affiliated companies. If you wish "
        "to opt out of such sharing for marketing purposes please say opt out or press one. "
        "Your election will take effect within 30 days.",
        {"category": "compliance_scripts", "subcategory": "privacy_script",
         "keywords": "privacy opt out affiliated non-affiliated 30 days marketing sharing"},
    ),
    (
        "Relationship Restriction Script: For your security account information and policy "
        "changes may only be discussed with the verified policy owner or an authorized "
        "representative. If you are calling on behalf of the policy owner you must provide "
        "written authorization before we can release account details.",
        {"category": "compliance_scripts", "subcategory": "relationship_restriction",
         "keywords": "relationship restriction owner authorized representative written authorization security"},
    ),
    (
        "Document Delivery Restriction Script: For your security and privacy we are only able "
        "to send policy documents to the mailing address we have on file for your account. "
        "We are not able to send documents by email or to a temporary address. If your address "
        "has changed please update it with us first and we will then process your document request.",
        {"category": "compliance_scripts", "subcategory": "document_delivery_restriction",
         "keywords": "document delivery mail only no email address on file security privacy"},
    ),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def build_documents() -> list[Document]:
    docs = []
    for i, (content, meta) in enumerate(KNOWLEDGE_BASE):
        docs.append(Document(
            page_content=content.strip(),
            metadata={**meta, "chunk_id": i, "source": "seed_knowledge.py"},
        ))
    return docs


def print_summary(docs: list[Document]) -> None:
    from collections import Counter
    cats = Counter(d.metadata["category"] for d in docs)
    print(f"\n{'='*60}")
    print(f"  insuranceCompany Knowledge Base  —  {len(docs)} chunks")
    print(f"{'='*60}")
    for cat, count in sorted(cats.items()):
        print(f"  {cat:<38} {count:>2} chunks")
    print(f"{'='*60}\n")


async def seed(reset: bool = False, dry_run: bool = False) -> None:
    docs = build_documents()
    print_summary(docs)

    if dry_run:
        print("[DRY RUN] Sample chunks:\n")
        for d in docs[:5]:
            print(f"  [{d.metadata['category']} / {d.metadata['subcategory']}]")
            print(f"  {d.page_content[:130]}...")
            print()
        print(f"[DRY RUN] {len(docs)} total chunks. Nothing written to DB.")
        return

    print("Connecting to PostgreSQL + pgvector ...")
    try:
        from config import settings
        from langchain_community.vectorstores import PGVector
        from langchain_openai import OpenAIEmbeddings
        from services.rag import COLLECTION_NAME

        embeddings = OpenAIEmbeddings(
            model=settings.openai_embedding_model,
            openai_api_key=settings.openai_api_key,
        )

        if reset:
            print("--reset: dropping existing collection ...")
            store = PGVector(
                collection_name=COLLECTION_NAME,
                connection_string=settings.database_url,
                embedding_function=embeddings,
            )
            store.delete_collection()
            print("  Collection dropped.\n")

        print(f"Embedding and inserting {len(docs)} chunks into '{COLLECTION_NAME}' ...")
        print("  (Calls OpenAI Embeddings API — may take 20-40 seconds)\n")

        store = PGVector.from_documents(
            documents=docs,
            embedding=embeddings,
            collection_name=COLLECTION_NAME,
            connection_string=settings.database_url,
        )

        print(f"\nDone. {len(docs)} knowledge chunks written to pgvector.")
        print("The FAQ node will now use this knowledge base for all RAG lookups.\n")

        # Spot-check
        tests = [
            "what happens if I miss a payment",
            "can you email my policy documents",
            "how do I change my beneficiary",
            "what is a policy loan",
        ]
        print("Spot-check queries:")
        for q in tests:
            results = store.similarity_search(q, k=1)
            top = results[0] if results else None
            hit = f"[{top.metadata['subcategory']}] {top.page_content[:80]}..." if top else "no result"
            print(f"  Q: {q!r}")
            print(f"     -> {hit}\n")

    except Exception as e:
        print(f"\nERROR: {e}")
        print("\nMake sure PostgreSQL + pgvector is running:")
        print("  docker-compose up -d postgres")
        print("  (wait ~5 seconds then retry)")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the insuranceCompany RAG knowledge base")
    parser.add_argument("--reset",   action="store_true",
                        help="Drop and recreate the collection before seeding")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print chunks without writing to DB")
    args = parser.parse_args()
    asyncio.run(seed(reset=args.reset, dry_run=args.dry_run))
