# Politique de sécurité

## Signaler une vulnérabilité

Dans GitHub, ouvrez **Security and quality** (ou **Security**), puis **Advisories** et **Report a vulnerability**. Le signalement privé permet au mainteneur de vérifier et de corriger le problème avant une éventuelle publication.

Indiquez la skill ou le script concerné (`check-release.ps1`, `SKILL.md`, etc.), le commit exact et les étapes de reproduction avec des données exclusivement fictives (`CANARY_TEST_`). N’ajoutez jamais de secrets réels, de données personnelles ni de chemins privés.

Pour un défaut ordinaire sans enjeu de sécurité, utilisez **Issues**.

## Versions suivies

Les correctifs sont publiés sur la branche principale `main`. Vérifiez si le problème persiste sur cette branche.
