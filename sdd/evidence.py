"""Evidence sources.

The `no_hires_since_posting` falsifier needs a fact the signal source does not
carry: whether anyone was hired into the function since the posting went up. v1
**consumes** that evidence, it does not gather it. The source is an interface so
a later adapter (a roster scraper, a job-changes feed) can replace the file
without any check changing.

The distinction that matters everywhere downstream:

* a company **present** in the file was looked at, and ``hires`` is what was found
* a company **absent** returns ``None``, meaning "nobody looked"

Absent must never read as "no hires." That collapse is exactly the failure this
whole tool exists to prevent, so it is a type-level distinction, not a comment.
"""

import json

from .model import DriftError, parse_date


class NullEvidenceSource:
    """Knows nothing about anybody. Every check that needs it fails closed."""

    name = "none"

    def observations(self, company):
        return None


class FileEvidenceSource:
    """Hire observations from a JSON file.

    Shape::

        {"companies": {"<company>": {"checked_through": "YYYY-MM-DD",
                                     "hires": [{"function": "gtm",
                                                "date": "YYYY-MM-DD",
                                                "source": "..."}]}}}
    """

    name = "file"

    def __init__(self, path):
        self.path = path
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            raise DriftError("evidence file not found: %s" % path)
        except json.JSONDecodeError as exc:
            raise DriftError("evidence file is not valid JSON (%s): %s" % (path, exc))

        companies = raw.get("companies")
        if not isinstance(companies, dict):
            raise DriftError(
                "evidence file must carry a 'companies' object: %s" % path
            )

        self._companies = {}
        for company, record in companies.items():
            hires = []
            for hire in record.get("hires", []):
                hires.append(
                    {
                        "function": hire.get("function"),
                        "date": parse_date(
                            hire.get("date"), "hire date for %s" % company
                        ),
                        "source": hire.get("source", ""),
                    }
                )
            checked_through = record.get("checked_through")
            self._companies[company] = {
                "hires": hires,
                "checked_through": (
                    parse_date(checked_through, "checked_through for %s" % company)
                    if checked_through
                    else None
                ),
            }

    def observations(self, company):
        return self._companies.get(company)
