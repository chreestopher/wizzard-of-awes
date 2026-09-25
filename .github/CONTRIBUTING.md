# Contributing

Submit changes through a pull request to `main`. Force pushes and branch deletion
are disabled on `main`, including for administrators. Owners may merge their own
pull requests without requiring a second reviewer.

Do not commit credentials, private contact information, customer submissions,
private backups, or deployment logs. Use your GitHub noreply address for commit
metadata. Before using GitHub's web editor or merge button, enable **Keep my email
addresses private** at https://github.com/settings/emails.

Run `node --check site/app.js` and `python -m unittest discover -s tests` before
submitting changes. Website changes deploy automatically after a merge into `main`;
backend and infrastructure deployment is described in the README.
