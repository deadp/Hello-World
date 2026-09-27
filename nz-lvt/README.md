# Land value tax analysis for New Zealand

Georgist-style land value rating analysis for New Zealand councils, following the method
in Lars Doucet's [*Does Georgism Work? Five Years Later*](https://www.astralcodexten.com/p/does-georgism-work-five-years-later)
and the Center for Land Economics' LVTShift work.

New Zealand is well suited to this. Every rating unit already has a statutory land value
and capital value, revalued every three years. Under the Local Government (Rating) Act
2002, councils can already choose to rate on land value without new law. The analysis is
therefore done council by council for rates, because each council sets its own rates;
the national folder models a central-government land value tax.

| Council | Status |
|---|---|
| [Wellington City](wellington/) | Done: revenue-neutral CV→LV general-rate model, suburb impacts, vacant-land uniformity check, block/SA1 analysis |
| [National](national/) | First pass: The Opportunity Party's 2026 Tax Reset (LVT + Citizen's Income + income tax) by SA2, using ~2M rating units from 57 councils' public layers and the 2023 Census |

Setup: `pip install -r requirements.txt`, then follow each council's README.
