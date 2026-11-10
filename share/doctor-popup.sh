#!/bin/bash
# `force doctor --fix` in a popup; stays open until a key is pressed.
"$HOME/.local/bin/force" doctor --fix
echo; echo "press any key"; read -rsn1
