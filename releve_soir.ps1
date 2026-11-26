# Releve du soir, dans le meme creneau horaire que la MEP (22h) : remplace le relevé
# toutes les 2h en journee, qui n'a pas de sens pour comparer a un instant precis.
# Tourne chaque soir a 22h jusqu'a la MEP (01/12) ; la derniere execution avant le
# --marquer-ng du jour J sert de reference Legacy figee.
Set-Location -Path $PSScriptRoot
py -I verifier_criteres_rollback.py
py -I qualitatif_capture.py avant
