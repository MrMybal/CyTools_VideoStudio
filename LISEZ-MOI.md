# CyTools VideoStudio

Studio vidéo pour la génération IA, les workflows ComfyUI, le motion design, l’amélioration RTX/DLSS, la stéréo 3D et les gaussiennes. L’application repose sur CyToolsCore et expose ses opérations via MCP, SDK et CLI.

VideoStudio est distribué sous [GNU AGPL v3](LICENSE) (`AGPL-3.0-only`). Copyright © 2026 Cyberalien.

Décompressez le ZIP dans un dossier accessible en écriture. Depuis PowerShell, lancez une fois
`./app/install.ps1` avec Python 3.11 x64 installé, puis démarrez `CyTools_VideoStudio.exe`.
Dans un checkout source, passez `-CoreWheel` avec le wheel CyToolsCore 0.12.0 approprié.
Pour construire le lanceur à partir de ses sources, exécutez `app/build_launcher.ps1`
depuis un terminal développeur Visual Studio x64.

Dans **Options**, sélectionnez **Français** si vous le souhaitez. Dans **Composants**, indiquez
le dossier des exécutables FFmpeg, les applications vidéo déjà installées et la connexion ComfyUI
de votre choix. Pour faire fonctionner des modèles dans une instance ComfyUI privée du studio,
installez cette instance depuis son panneau puis référencez les fichiers ou bibliothèques de modèles
déjà présents. Vous pouvez télécharger les poids séparément depuis le même onglet.

Les résultats, configurations et journaux du studio résident dans `data/`. Le Python privé,
les composants téléchargés et les mises à jour résident dans `runtime/`.

Sous Linux, installez le wheel Linux de CyToolsCore via `sh app/install_linux.sh`, puis lancez
`sh CyTools_VideoStudio.sh`. Le parcours Linux reste à valider.

Les opérations, exemples SDK/CLI/MCP et restrictions de plateforme sont dans
[le guide technique](app/README.md). Pour l’état des fonctionnalités, consultez
[le README français](README.fr.md) et [le rapport de validation](app/Validation.md).
