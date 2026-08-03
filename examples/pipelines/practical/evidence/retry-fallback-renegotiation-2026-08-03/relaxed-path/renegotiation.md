## ORIGINAL GOAL
Implement full RFC 5322 email address validation using a regular expression, covering all constructs defined in the standard including quoted strings, domain literals, comments, folding whitespace, and the complete set of allowed special characters.

## RELAXED CRITERIA
Validate common email formats only, specifically the user@domain.com style. The local part consists of alphanumeric characters and common symbols (dots, hyphens, underscores, plus signs). The domain part consists of dot-separated alphanumeric labels with an optional hyphen, ending in a recognizable TLD. Quoted local parts, IP address domain literals, and comments are out of scope.

## REASON
This renegotiation was triggered by budget exhaustion: the validate_gate ran 4 times without the implementation passing validation. The full RFC 5322 regex repeatedly failed the test suite and could not be corrected within the allowed retry budget.

## WHAT THIS RUN WILL ACHIEVE
A regex that correctly matches the most common real-world email address formats of the form user@domain.com, including dots, hyphens, underscores, and plus signs in the local part, and multi-level domains (e.g. user@mail.example.co.uk). It will reliably accept valid everyday addresses and reject clearly malformed ones.

## WHAT THIS RUN WILL NOT ACHIEVE
Full RFC 5322 compliance. Specifically, the following edge cases will not be supported: quoted string local parts (e.g. "john doe"@example.com), domain literals and IP address notation (e.g. user@[192.168.1.1]), comments in addresses (e.g. (comment)user@example.com), folding whitespace, obsolete address syntax, and the full range of special characters permitted by the RFC in unquoted local parts.
