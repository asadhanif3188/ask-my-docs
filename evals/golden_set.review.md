# Golden set draft — 50 cases for review

`evals/golden_set.staged.jsonl` is the machine-readable version. Nothing is committed to
`golden_set.jsonl` yet, so run_evals.py still exits 2 on the placeholders.

Verification already run against the DB: every `expected_passage` is a verbatim substring of its
cited chunk, every source_document/page matches that chunk, and every number in a ground_truth
appears in a cited chunk (except derived arithmetic in synthesis cases, flagged below).


## factual (20 cases)

### g001  ·  factual
**Q:** In Apple's fiscal 2022 Form 10-K, how many shareholders of record were there as of October 14, 2022?

**Ground truth:** Apple reported 23,838 shareholders of record as of October 14, 2022.

**Source:** `corpus\AAPL_10K_2022-10-28.pdf` p[52]  ·  chunk_ids `[6783]`

**Verbatim supporting passage (copied from the ingested chunk):**

> As of October 14, 2022, there were 23,838 shareholders of record.

### g002  ·  factual
**Q:** What was the total of Apple's cash, cash equivalents and unrestricted marketable securities as of September 24, 2022?

**Ground truth:** Apple's cash, cash equivalents and unrestricted marketable securities totaled $156.4 billion as of September 24, 2022.

**Source:** `corpus\AAPL_10K_2022-10-28.pdf` p[66]  ·  chunk_ids `[6798]`

**Verbatim supporting passage (copied from the ingested chunk):**

> which totaled $156.4 billion as of September 24, 2022

### g003  ·  factual
**Q:** According to Apple's fiscal 2023 Form 10-K, how many shareholders of record did the company have as of October 20, 2023?

**Ground truth:** Apple had 23,763 shareholders of record as of October 20, 2023.

**Source:** `corpus\AAPL_10K_2023-11-03.pdf` p[50]  ·  chunk_ids `[6965]`

**Verbatim supporting passage (copied from the ingested chunk):**

> As of October 20, 2023, there were 23,763 shareholders of record.

### g004  ·  factual
**Q:** What was the total amount of Apple's gross unrecognized tax benefits as of September 30, 2023?

**Ground truth:** As of September 30, 2023, Apple's total gross unrecognized tax benefits were $19.5 billion, of which $9.5 billion, if recognized, would impact the effective tax rate.

**Source:** `corpus\AAPL_10K_2023-11-03.pdf` p[110]  ·  chunk_ids `[7032]`

**Verbatim supporting passage (copied from the ingested chunk):**

> the total amount of gross unrecognized tax benefits was $19.5

### g005  ·  factual
**Q:** In Apple's fiscal 2024 Form 10-K, what maximum one-day loss in fair value on foreign currency derivative positions did the VAR model estimate with 95% confidence as of September 28, 2024?

**Ground truth:** Apple estimated, with 95% confidence, a maximum one-day loss in fair value of $538 million as of September 28, 2024.

**Source:** `corpus\AAPL_10K_2024-11-01.pdf` p[64]  ·  chunk_ids `[7173]`

**Verbatim supporting passage (copied from the ingested chunk):**

> maximum one-day loss in fair

### g006  ·  factual
**Q:** According to Apple's fiscal 2025 Form 10-K, what fine did the European Commission impose on Apple on April 23, 2025 in the Article 5(4) Digital Markets Act investigation?

**Ground truth:** On April 23, 2025 the Commission fined Apple 500 million euros in the Article 5(4) Investigation and issued a cease and desist order.

**Source:** `corpus\AAPL_10K_2025-10-31.pdf` p[47]  ·  chunk_ids `[7337]`

**Verbatim supporting passage (copied from the ingested chunk):**

> On April 23, 2025, the Commission fined the Company

### g007  ·  factual
**Q:** What was Apple's research and development expense in fiscal 2025, and by what percentage did it change from fiscal 2024?

**Ground truth:** Apple's research and development expense was $34,550 million in fiscal 2025, a 10% increase over fiscal 2024.

**Source:** `corpus\AAPL_10K_2025-10-31.pdf` p[58]  ·  chunk_ids `[7352]`

**Verbatim supporting passage (copied from the ingested chunk):**

> Research and development
$34,550

> [!] REVIEW: table-derived number (flattened row) — check year mapping

### g008  ·  factual
**Q:** According to Microsoft's fiscal year 2022 Form 10-K, approximately how many members does LinkedIn have?

**Ground truth:** As stated in Microsoft's fiscal year 2022 10-K, LinkedIn has over 850 million members.

**Source:** `corpus\MSFT_10K_2022-07-28.pdf` p[65]  ·  chunk_ids `[7516]`

**Verbatim supporting passage (copied from the ingested chunk):**

> LinkedIn has over 850 million members

### g009  ·  factual
**Q:** In Microsoft's fiscal year 2022 10-K, citing the 2021 Diversity and Inclusion Report, how much did all racial and ethnic minority employees in the U.S. combined earn for every $1.000 earned by their white counterparts?

**Ground truth:** Microsoft's fiscal year 2022 10-K reports that all racial and ethnic minority employees in the U.S. combined earn $1.006 for every $1.000 earned by their white counterparts.

**Source:** `corpus\MSFT_10K_2022-07-28.pdf` p[62]  ·  chunk_ids `[7510]`

**Verbatim supporting passage (copied from the ingested chunk):**

> combined earn $1.006 for every $1.000 earned by their white counterparts

### g010  ·  factual
**Q:** How much did Microsoft return to shareholders in the form of share repurchases and dividends during the fourth quarter of fiscal year 2023?

**Ground truth:** Microsoft returned $9.7 billion to shareholders through share repurchases and dividends in the fourth quarter of fiscal year 2023.

**Source:** `corpus\MSFT_10K_2023-07-27.pdf` p[94]  ·  chunk_ids `[7743]`

**Verbatim supporting passage (copied from the ingested chunk):**

> We returned $9.7 billion to shareholders in the form of share repurchases and dividends in the fourth

### g011  ·  factual
**Q:** According to Microsoft's fiscal year 2023 10-K, approximately how many acres of land does Microsoft own at its corporate headquarters?

**Ground truth:** Microsoft's fiscal year 2023 10-K states it owns approximately 530 acres of land at its corporate headquarters, on which approximately 11 million square feet of owned space is situated.

**Source:** `corpus\MSFT_10K_2023-07-27.pdf` p[91]  ·  chunk_ids `[7739]`

**Verbatim supporting passage (copied from the ingested chunk):**

> approximately 11 million square feet of owned space situated on approximately 530 acres of land we

### g012  ·  factual
**Q:** How many people did Microsoft employ on a full-time basis as of June 30, 2024, according to its fiscal year 2024 10-K?

**Ground truth:** As of June 30, 2024, Microsoft employed approximately 228,000 people on a full-time basis.

**Source:** `corpus\MSFT_10K_2024-07-30.pdf` p[58]  ·  chunk_ids `[7817]`

**Verbatim supporting passage (copied from the ingested chunk):**

> As of June 30, 2024, we employed approximately 228,000 people on a full-time basis

### g013  ·  factual
**Q:** In Microsoft's fiscal year 2024 10-K, which two Microsoft offerings did the European Commission designate as core platform services under the EU Digital Markets Act?

**Ground truth:** The European Commission designated Windows and LinkedIn as core platform services subject to obligations under the EU Digital Markets Act.

**Source:** `corpus\MSFT_10K_2024-07-30.pdf` p[83]  ·  chunk_ids `[7878]`

**Verbatim supporting passage (copied from the ingested chunk):**

> has designated Windows and LinkedIn as

### g014  ·  factual
**Q:** By how much did Microsoft's general and administrative expenses change in fiscal year 2025 compared with fiscal year 2024?

**Ground truth:** In fiscal year 2025, Microsoft's general and administrative expenses decreased $386 million, or 5%, driven by Gaming, including the impact of the Activision Blizzard acquisition.

**Source:** `corpus\MSFT_10K_2025-07-30.pdf` p[98]  ·  chunk_ids `[8061]`

**Verbatim supporting passage (copied from the ingested chunk):**

> General and administrative expenses decreased $386 million or 5% driven by Gaming

### g015  ·  factual
**Q:** In NVIDIA's fiscal year 2023 10-K, what was Gaming revenue for fiscal year 2023 and how did it change versus fiscal year 2022?

**Ground truth:** NVIDIA's Gaming revenue for fiscal year 2023 was $9.07 billion, down 27% from fiscal year 2022.

**Source:** `corpus\NVDA_10K_2023-02-24.pdf` p[79]  ·  chunk_ids `[8210]`

**Verbatim supporting passage (copied from the ingested chunk):**

> Gaming revenue for fiscal year 2023 was $9.07 billion, down 27% from fiscal year 2022.

### g016  ·  factual
**Q:** How many shares did NVIDIA withhold through net share settlements during fiscal year 2023, and what was their total value?

**Ground truth:** During fiscal year 2023 NVIDIA withheld approximately 8 million shares, for a total value of $1.48 billion, through net share settlements.

**Source:** `corpus\NVDA_10K_2023-02-24.pdf` p[73]  ·  chunk_ids `[8201]`

**Verbatim supporting passage (copied from the ingested chunk):**

> we withheld approximately 8 million shares for a total value of $1.48

### g017  ·  factual
**Q:** According to NVIDIA's 10-K filed February 21, 2024, in what year did Colette M. Kress join NVIDIA and in what role?

**Ground truth:** Colette M. Kress joined NVIDIA in 2013 as Executive Vice President and Chief Financial Officer.

**Source:** `corpus\NVDA_10K_2024-02-21.pdf` p[46]  ·  chunk_ids `[8288]`

**Verbatim supporting passage (copied from the ingested chunk):**

> joined NVIDIA in 2013 as Executive Vice President and Chief Financial Officer.

### g018  ·  factual
**Q:** How many employees did NVIDIA have at the end of fiscal year 2025, in how many countries, and how were they split between R&D and other functions?

**Ground truth:** As of the end of fiscal year 2025, NVIDIA had approximately 36,000 employees in 38 countries, of whom 27,100 were engaged in research and development and 8,900 in sales, marketing, operations, and administrative positions.

**Source:** `corpus\NVDA_10K_2025-02-26.pdf` p[45]  ·  chunk_ids `[8436]`

**Verbatim supporting passage (copied from the ingested chunk):**

> we had approximately 36,000 employees

### g019  ·  factual
**Q:** In NVIDIA's fiscal year 2025 10-K, what are the expiration dates of the company's currently issued patents?

**Ground truth:** NVIDIA's currently issued patents have expiration dates from February 2025 to June 2045.

**Source:** `corpus\NVDA_10K_2025-02-26.pdf` p[42]  ·  chunk_ids `[8428]`

**Verbatim supporting passage (copied from the ingested chunk):**

> Our currently issued patents have expiration dates from February 2025 to June 2045.

### g020  ·  factual
**Q:** According to NVIDIA's 10-K filed February 25, 2026, when did the U.S. government announce export restrictions targeting China's semiconductor and supercomputing industries?

**Ground truth:** In August 2022 the U.S. government announced export restrictions and export licensing requirements targeting China's semiconductor and supercomputing industries.

**Source:** `corpus\NVDA_10K_2026-02-25.pdf` p[42]  ·  chunk_ids `[8575]`

**Verbatim supporting passage (copied from the ingested chunk):**

> announced export restrictions and export licensing


## semantic (15 cases)

### g021  ·  semantic
**Q:** Per Apple's fiscal 2024 annual report, which nations host the external firms that put together nearly all of its hardware?

**Ground truth:** Substantially all of Apple's manufacturing is performed in whole or in part by outsourcing partners located primarily in China mainland, India, Japan, South Korea, Taiwan and Vietnam.

**Source:** `corpus\AAPL_10K_2024-11-01.pdf` p[35]  ·  chunk_ids `[7114]`

**Verbatim supporting passage (copied from the ingested chunk):**

> partners located primarily in China mainland, India, Japan, South Korea, Taiwan and Vietnam

### g022  ·  semantic
**Q:** In Apple's fiscal 2022 annual report, on what day does the company's accounting calendar close, and how long is that reporting cycle?

**Ground truth:** Apple's fiscal year is the 52- or 53-week period that ends on the last Saturday of September.

**Source:** `corpus\AAPL_10K_2022-10-28.pdf` p[87]  ·  chunk_ids `[6824]`

**Verbatim supporting passage (copied from the ingested chunk):**

> 52- or 53-week period that ends on the last Saturday of September

### g023  ·  semantic
**Q:** Why does Apple's fiscal 2023 filing say the firm is a more appealing mark for cyber criminals than most other businesses?

**Ground truth:** Apple says it is at a relatively greater risk of being targeted because of its high profile and the value of the confidential information it creates, owns, manages, stores and processes.

**Source:** `corpus\AAPL_10K_2023-11-03.pdf` p[42]  ·  chunk_ids `[6944]`

**Verbatim supporting passage (copied from the ingested chunk):**

> risk of being targeted because of its high profile and the value of the confidential information

### g024  ·  semantic
**Q:** According to Apple's fiscal 2025 filing, are most of the plants that build its parts situated inside American borders?

**Ground truth:** No. A majority of Apple's supplier facilities, including manufacturing and assembly sites, are located outside the U.S.

**Source:** `corpus\AAPL_10K_2025-10-31.pdf` p[31]  ·  chunk_ids `[7291]`

**Verbatim supporting passage (copied from the ingested chunk):**

> majority of the Company's supplier facilities, including manufacturing and assembly sites, are located

### g025  ·  semantic
**Q:** In what city was Apple's principal corporate base situated at the close of fiscal 2024?

**Ground truth:** Apple's headquarters is located in Cupertino, California.

**Source:** `corpus\AAPL_10K_2024-11-01.pdf` p[48]  ·  chunk_ids `[7152]`

**Verbatim supporting passage (copied from the ingested chunk):**

> The Company's headquarters is located in Cupertino, California.

### g026  ·  semantic
**Q:** In Microsoft's fiscal 2022 annual report, roughly how big is the catalog of playable titles that members of its Xbox subscription offering can reach?

**Ground truth:** Xbox Game Pass gives its subscribers access to a curated library of over 100 first- and third-party console and PC titles, per Microsoft's fiscal year 2022 10-K.

**Source:** `corpus\MSFT_10K_2022-07-28.pdf` p[69]  ·  chunk_ids `[7524]`

**Verbatim supporting passage (copied from the ingested chunk):**

> access to a curated library of over 100 first- and third-party console

### g027  ·  semantic
**Q:** Which country hosts the back-office hub that Microsoft's fiscal 2023 annual report says serves its business outside the Americas?

**Ground truth:** Ireland: Microsoft's fiscal year 2023 10-K states that its center in Ireland supports the African, Asia-Pacific, European, and Middle East regions.

**Source:** `corpus\MSFT_10K_2023-07-27.pdf` p[68]  ·  chunk_ids `[7679]`

**Verbatim supporting passage (copied from the ingested chunk):**

> The center in Ireland supports the African, Asia-Pacific, European, and Middle East

### g028  ·  semantic
**Q:** Which outside firms does Microsoft name as rivals to its public-cloud platform in its fiscal year 2023 annual report?

**Ground truth:** Microsoft's fiscal year 2023 10-K says Azure faces diverse competition from companies such as Amazon, Google, IBM, Oracle, VMware, and open source offerings.

**Source:** `corpus\MSFT_10K_2023-07-27.pdf` p[64]  ·  chunk_ids `[7671]`

**Verbatim supporting passage (copied from the ingested chunk):**

> Azure faces diverse competition from companies such as Amazon, Google, IBM, Oracle, VMware, and

### g029  ·  semantic
**Q:** According to Microsoft's fiscal year 2024 filing, who physically assembles the hardware the company sells?

**Ground truth:** Microsoft's devices are primarily manufactured by third-party contract manufacturers, according to its fiscal year 2024 10-K.

**Source:** `corpus\MSFT_10K_2024-07-30.pdf` p[65]  ·  chunk_ids `[7834]`

**Verbatim supporting passage (copied from the ingested chunk):**

> Our devices are primarily manufactured by third-party contract manufacturers.

### g030  ·  semantic
**Q:** Why does Microsoft's fiscal year 2025 annual report treat its northern-California sites as vulnerable to a major tremor?

**Ground truth:** Because Microsoft's operations in the Silicon Valley area of California (like those in the Seattle, Washington area) are in seismically active regions, per its fiscal year 2025 10-K.

**Source:** `corpus\MSFT_10K_2025-07-30.pdf` p[79]  ·  chunk_ids `[8030]`

**Verbatim supporting passage (copied from the ingested chunk):**

> operations in the Silicon Valley area of California, both of which are seismically active regions.

### g031  ·  semantic
**Q:** Per NVIDIA's fiscal year 2023 annual report, in which U.S. state was the firm originally founded, and to which state did it later shift its legal domicile?

**Ground truth:** NVIDIA was incorporated in California in April 1993 and reincorporated in Delaware in April 1998.

**Source:** `corpus\NVDA_10K_2023-02-24.pdf` p[36]  ·  chunk_ids `[8105]`

**Verbatim supporting passage (copied from the ingested chunk):**

> NVIDIA was incorporated in California in April 1993 and

### g032  ·  semantic
**Q:** In the annual report NVIDIA filed on February 21, 2024, which single overseas territory does the firm flag as the one whose disruption would most jeopardize the steady flow of parts it obtains from partners abroad?

**Ground truth:** NVIDIA says its business depends on receiving consistent and reliable supply from its overseas partners, especially in Taiwan, and that new restrictions affecting supply of components, parts, or services from Taiwan would negatively impact its business and financial results.

**Source:** `corpus\NVDA_10K_2024-02-21.pdf` p[68]  ·  chunk_ids `[8346]`

**Verbatim supporting passage (copied from the ingested chunk):**

> our business depends on our ability to receive consistent and reliable supply from our overseas

### g033  ·  semantic
**Q:** Which globally recognized benchmark for protecting data does NVIDIA say its internal safeguarding program broadly conforms to, in the 10-K filed February 21, 2024?

**Ground truth:** NVIDIA's information security management program generally follows processes outlined in frameworks such as the ISO 27001 international standard for Information Security.

**Source:** `corpus\NVDA_10K_2024-02-21.pdf` p[73]  ·  chunk_ids `[8361]`

**Verbatim supporting passage (copied from the ingested chunk):**

> follows processes outlined in frameworks such as the ISO 27001 international standard for Information

### g034  ·  semantic
**Q:** Roughly how many people does NVIDIA say it employs in the Middle Eastern area whose wartime situation it is watching in its fiscal year 2025 report, and which product line do those staff mainly work on?

**Ground truth:** NVIDIA reports approximately 4,700 employees in the region in and around Israel, who primarily support the research and development, operations, and sales and marketing of its networking products.

**Source:** `corpus\NVDA_10K_2025-02-26.pdf` p[61]  ·  chunk_ids `[8473]`

**Verbatim supporting passage (copied from the ingested chunk):**

> including the health and safety of our approximately 4,700 employees in the region who primarily

### g035  ·  semantic
**Q:** In the annual report NVIDIA filed on February 25, 2026, from which city are a large share of the company's goods stored and shipped out?

**Ground truth:** NVIDIA states that a substantial portion of its products are warehoused in and distributed from Hong Kong, and that export controls may disrupt that supply and distribution chain.

**Source:** `corpus\NVDA_10K_2026-02-25.pdf` p[69]  ·  chunk_ids `[8644]`

**Verbatim supporting passage (copied from the ingested chunk):**

> in and distributed from Hong Kong.


## synthesis (10 cases)

### g036  ·  synthesis
**Q:** How much did Apple's research and development expense grow in dollar terms from fiscal 2022 to fiscal 2025?

**Ground truth:** Apple's research and development expense rose from $26,251 million in fiscal 2022 to $34,550 million in fiscal 2025, an increase of $8,299 million.

**Source:** `corpus\AAPL_10K_2025-10-31.pdf` p[58]  ·  chunk_ids `[7352, 7164]`

**Verbatim supporting passage (copied from the ingested chunk):**

> Research and development
$34,550

> [!] REVIEW: derived: 34,550 - 26,251 = 8,299

### g037  ·  synthesis
**Q:** Did Apple's count of shareholders of record rise or fall between the record dates disclosed in its fiscal 2022 and fiscal 2023 Form 10-K filings, and by how many?

**Ground truth:** It fell by 75, from 23,838 shareholders of record as of October 14, 2022 to 23,763 as of October 20, 2023.

**Source:** `corpus\AAPL_10K_2022-10-28.pdf` p[52, 50]  ·  chunk_ids `[6783, 6965]`

**Verbatim supporting passage (copied from the ingested chunk):**

> As of October 14, 2022, there were 23,838 shareholders of record.

> [!] REVIEW: derived: 23,838 - 23,763 = 75

### g038  ·  synthesis
**Q:** How much larger were Apple's total gross unrecognized tax benefits as of September 27, 2025 compared with September 30, 2023?

**Ground truth:** They were $3.7 billion larger: $23.2 billion as of September 27, 2025 versus $19.5 billion as of September 30, 2023.

**Source:** `corpus\AAPL_10K_2025-10-31.pdf` p[106, 110]  ·  chunk_ids `[7407, 7032]`

**Verbatim supporting passage (copied from the ingested chunk):**

> the total amount of gross unrecognized tax benefits was $23.2

> [!] REVIEW: derived: 23.2 - 19.5 = 3.7

### g039  ·  synthesis
**Q:** Which competitor did Microsoft add to the named rivals of its Search and news advertising business in the fiscal year 2024 10-K that was not listed in the fiscal year 2023 10-K?

**Ground truth:** OpenAI. The fiscal year 2023 10-K said Search and news advertising competes with Google and a wide array of websites, social platforms like Meta, and portals, while the fiscal year 2024 10-K lists Google, OpenAI, and those same other players.

**Source:** `corpus\MSFT_10K_2023-07-27.pdf` p[68, 65]  ·  chunk_ids `[7679, 7834]`

**Verbatim supporting passage (copied from the ingested chunk):**

> Our Search and news advertising business competes with Google and a wide array of websites, social

### g040  ·  synthesis
**Q:** How did the number of registered holders of record of Microsoft common stock change between the fiscal year 2024 10-K (as of July 25, 2024) and the fiscal year 2025 10-K (as of July 24, 2025)?

**Ground truth:** Registered holders of record fell from 81,346 on July 25, 2024 to 77,014 on July 24, 2025, a decrease of 4,332 holders.

**Source:** `corpus\MSFT_10K_2024-07-30.pdf` p[94, 85]  ·  chunk_ids `[7905, 8043]`

**Verbatim supporting passage (copied from the ingested chunk):**

> there were 81,346 registered holders of record of our common stock.

> [!] REVIEW: derived: 81,346 - 77,014 = 4,332

### g041  ·  synthesis
**Q:** In Microsoft's fiscal year 2025, how much did Microsoft Cloud revenue grow, and what caused the Microsoft Cloud gross margin percentage to decline?

**Ground truth:** Microsoft Cloud revenue increased 23% to $168.9 billion in fiscal year 2025, while Microsoft Cloud gross margin percentage decreased to 69% driven by the impact of scaling AI infrastructure, offset in part by efficiency gains in Azure.

**Source:** `corpus\MSFT_10K_2025-07-30.pdf` p[87, 92]  ·  chunk_ids `[8046, 8054]`

**Verbatim supporting passage (copied from the ingested chunk):**

> Microsoft Cloud revenue increased 23% to $168.9 billion.

> [!] REVIEW: table-derived number (flattened row) — check year mapping

### g042  ·  synthesis
**Q:** How did the number of applications supported by NVIDIA's computing platform change between the fiscal year 2023 10-K and the fiscal year 2025 10-K?

**Ground truth:** NVIDIA reported support for more than 2,800 applications in its fiscal year 2023 10-K and more than 4,400 applications in its fiscal year 2025 10-K.

**Source:** `corpus\NVDA_10K_2023-02-24.pdf` p[36, 36]  ·  chunk_ids `[8105, 8409]`

**Verbatim supporting passage (copied from the ingested chunk):**

> With support for more than 2,800 applications

### g043  ·  synthesis
**Q:** For NVIDIA's fiscal year 2023, how did Gaming revenue compare with the company's total revenue for that year?

**Ground truth:** In fiscal year 2023 NVIDIA's total revenue was $26,974 million and Gaming revenue was $9.07 billion, so Gaming accounted for roughly one third of total revenue.

**Source:** `corpus\NVDA_10K_2023-02-24.pdf` p[77, 79]  ·  chunk_ids `[8207, 8210]`

**Verbatim supporting passage (copied from the ingested chunk):**

> Revenue
$26,974

> [!] REVIEW: table-derived number (flattened row) — check year mapping

### g044  ·  synthesis
**Q:** How did NVIDIA's reported share of the global TOP500 supercomputer list change between its fiscal year 2025 10-K and the 10-K it filed on February 25, 2026?

**Ground truth:** NVIDIA reported powering over 75% of the supercomputers on the global TOP500 list in its fiscal year 2025 10-K and over 78% in the 10-K filed February 25, 2026, an increase of about 3 percentage points.

**Source:** `corpus\NVDA_10K_2025-02-26.pdf` p[36, 36]  ·  chunk_ids `[8409, 8558]`

**Verbatim supporting passage (copied from the ingested chunk):**

> NVIDIA powers over 75% of the supercomputers on the global TOP500 list

### g045  ·  synthesis
**Q:** Combining NVIDIA's fiscal year 2023 10-K and the 10-K filed February 21, 2024, what did the Ethereum 2.0 merge mean for NVIDIA's Lite Hash Rate feature and for GPU usage in Ethereum mining?

**Ground truth:** NVIDIA's fiscal year 2023 10-K states that with the Ethereum 2.0 merge its Ampere and Ada Lovelace architectures no longer include Lite Hash Rate (LHR), and the 10-K filed February 21, 2024 states that the Ethereum 2.0 merge in 2022 has reduced and may in the future decrease the usage of GPUs for Ethereum mining.

**Source:** `corpus\NVDA_10K_2023-02-24.pdf` p[55, 53]  ·  chunk_ids `[8153, 8306]`

**Verbatim supporting passage (copied from the ingested chunk):**

> merge, NVIDIA Ampere and Ada Lovelace architectures no longer include LHR.


## unanswerable (5 cases)

### g046  ·  unanswerable
**Q:** How many iPhone units did Apple sell in fiscal year 2024?

**Ground truth:** The provided documents do not contain this information. Apple's 10-K filings do not disclose iPhone unit sales volumes.

**Proof of absence:** Zero corpus matches for unit sales/units sold; Apple reports iPhone net sales in dollars only.

**Expected behaviour:** refusal — generator must return `{"claims":[]}`

### g047  ·  unanswerable
**Q:** What was Microsoft's Azure revenue in dollars for fiscal year 2025?

**Ground truth:** The provided documents do not contain this information. Microsoft discloses Azure growth rates, not standalone Azure revenue in dollars.

**Proof of absence:** Corpus states 'Azure and other cloud services revenue grew 34%' but never an absolute Azure dollar figure. Retrieval WILL surface an Azure chunk -> strongest hallucination trap.

**Expected behaviour:** refusal — generator must return `{"claims":[]}`

### g048  ·  unanswerable
**Q:** What is the annual base salary of NVIDIA's CEO Jensen Huang?

**Ground truth:** The provided documents do not contain this information. Executive compensation is incorporated by reference to the proxy statement and is not in these 10-K filings.

**Proof of absence:** Zero hits for Huang + salary/compensation figure; 'Item 11. Executive Compensation' appears only as a TOC heading.

**Expected behaviour:** refusal — generator must return `{"claims":[]}`

### g049  ·  unanswerable
**Q:** What were Apple's total net sales in fiscal year 2019?

**Ground truth:** The provided documents do not contain this information. The corpus covers Apple's fiscal 2022-2025 filings, which do not report fiscal 2019 net sales.

**Proof of absence:** Zero hits for a FY2019/FY2018 total net sales figure; out of corpus date range.

**Expected behaviour:** refusal — generator must return `{"claims":[]}`

### g050  ·  unanswerable
**Q:** What was Google's total revenue in 2024?

**Ground truth:** The provided documents do not contain this information. Google is named only as a competitor; these filings do not report Google's financial results.

**Proof of absence:** Zero hits for any Google/Alphabet revenue figure, though 'Google' appears in competition sections -> retrieval returns plausible but non-answering chunks.

**Expected behaviour:** refusal — generator must return `{"claims":[]}`
