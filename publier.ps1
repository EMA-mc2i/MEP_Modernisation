# Publie l'etat courant du dashboard sur GitHub Pages (push du depot local vers origin/main).
# Appele par les taches planifiees ponctuelles (19h30 et 22h le jour de la MEP) ; peut aussi
# etre lance a la main : powershell -File publier.ps1
Set-Location -Path $PSScriptRoot
py -I verifier_criteres_rollback.py
git add -A
$horodatage = Get-Date -Format "yyyy-MM-dd HH:mm"
git commit -m "Publication du $horodatage" -q
if ($LASTEXITCODE -ne 0) {
    Write-Output "Rien a committer (etat deja a jour) : push quand meme au cas ou."
}
git push
