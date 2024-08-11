#!/usr/bin/env	bash
#

# Sync everything but the .git folder, and don't delete it from the destination.
rsync -av --del --exclude='.git' --filter='P .git/' ../PyMake/ ../PyMake-ForGitHub


