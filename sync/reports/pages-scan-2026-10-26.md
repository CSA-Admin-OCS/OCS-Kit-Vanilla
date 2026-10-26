# Upstream scan

- upstream: https://github.com/Open-Coding-Society/pages.git @ 027793e511 (3994 files)
- kit: OCS-Kit-Pages (1124 files, catalog: yes)
- new: 3018, changed: 276, identical: 578, protected (kit-owned, skipped): 42, excluded by config: 80

## Suggested features from new upstream content (208 groups)

| group (upstream) | files | KB | suggested feature | new deps | missing refs | scrub findings |
|---|---:|---:|---|---:|---:|---|
| images/platformer | 128 | 30803 | game-engine | 0 | 0 | 0 |
| _projects/games/mansionGame | 114 | 53004 | game-engine | 3 | 0 | 4 (deny:[masked] 1, size:large 1) |
| images/capstone | 111 | 141749 | assignments | 0 | 0 | 4 (size:large 4) |
| _projects/games/cs-pathway | 99 | 65093 | game-engine | 5 | 5 | 10 (deny:[masked] 2) |
| _data (top-level files) | 93 | 379 | game-engine | 14 | 2 | 141 (deny:[masked] 79, deny:ocs-domain 62) |
| hacks/digital-famine -> features/hacks/digital-famine | 87 | 1971 | examples | 35 | 12 | 9 (deny:[masked] 1, secret:password-literal 6) |
| _posts/capstone | 85 | 503 | assignments | 185 | 4 | 89 (deny:[masked] 3, deny:ocs-domain 27) |
| assets/js/platformer3x | 85 | 455 | game-engine | 95 | 1 | 3 (deny:[masked] 1) |
| images (top-level files) | 78 | 128395 | assignments | 0 | 0 | 2 (size:large 2) |
| _includes (top-level files) | 70 | 631 | assignments | 91 | 6 | 168 (deny:[masked] 1, deny:ocs-domain 60) |
| _projects/lessons/java | 69 | 1647 | lessons | 0 | 6 | 4 (deny:[masked] 4) |
| _projects/systems/courses | 63 | 583 | lessons | 11 | 35 | 1 (deny:[masked] 1) |
| _projects/lessons/cs112 | 58 | 624 | lessons | 17 | 10 | 10 (deny:[masked] 7) |
| hacks/cs-portfolio-quest -> features/hacks/cs-portfolio-quest | 49 | 2424 | resume | 33 | 7 | 26 (deny:[masked] 18, deny:ocs-domain 2, secret:api-key-literal 2, secret:google-api-key 2, secret:password-literal 2) |
| _projects/systems/gamebuilder | 48 | 6586 | game-engine | 3 | 13 | 3 (deny:[masked] 2) |
| hacks/west-coast -> features/hacks/west-coast | 47 | 6856 | examples | 21 | 12 | 0 |
| _posts/Foundation | 45 | 387 | lessons | 26 | 28 | 38 (deny:[masked] 9, secret:google-api-key 4) |
| _projects/games/voidstriker | 44 | 9904 | game-engine | 0 | 0 | 0 |
| _projects/lessons/csa2 | 42 | 526 | lessons | 6 | 3 | 1 (deny:[masked] 1) |
| _notebooks/CSP | 40 | 811 | lessons | 28 | 2 | 78 (deny:[masked] 10, deny:ocs-domain 49, secret:password-literal 2) |
| _projects/games/kirby-minigames | 39 | 40420 | game-engine | 0 | 3 | 1 (size:large 1) |
| images/digital-famine | 36 | 17488 | examples | 0 | 0 | 0 |
| _posts/gamify | 35 | 411 | game-engine | 5 | 2 | 1 (deny:[masked] 1) |
| _projects/games/escape-the-tower | 35 | 14591 | game-engine | 0 | 0 | 0 |
| _includes/nav | 34 | 57 | lessons | 6 | 3 | 1 (deny:[masked] 1) |
| _projects/lessons/cs113 | 34 | 512 | lessons | 7 | 6 | 18 (deny:[masked] 4) |
| _projects/games/racing-game | 33 | 14198 | game-engine | 2 | 1 | 0 |
| _notebooks/CSSE | 32 | 366 | lessons | 7 | 13 | 12 (deny:[masked] 12) |
| assets/js/bigsix | 31 | 148 | resume | 0 | 0 | 2 (deny:ocs-domain 2) |
| scripts (top-level files) | 30 | 230 | lessons | 0 | 0 | 6 (deny:ocs-domain 6) |
| images/cs-portfolio-quest | 29 | 13374 | resume | 0 | 0 | 0 |
| tests (top-level files) | 29 | 147 | lessons | 20 | 9 | 1 (deny:[masked] 1) |
| _posts/CSH | 28 | 53 | social | 1 | 0 | 0 |
| _notebooks/Foundation | 26 | 499 | lessons | 6 | 6 | 40 (deny:[masked] 3) |
| images/binary | 24 | 4995 | lessons | 0 | 0 | 0 |
| _posts/CSP | 22 | 343 | lessons | 3 | 0 | 0 |
| _projects/systems/blogs | 22 | 197 | social | 12 | 19 | 2 (secret:google-api-key 2) |
| _projects/games/astronaut-platformer-game | 21 | 698 | game-engine | 0 | 7 | 0 |
| hacks (top-level files) | 21 | 187 | examples | 15 | 3 | 0 |
| images/FRQ_teach | 21 | 4498 | assignments | 0 | 0 | 0 |
| assets/pdfs | 20 | 9249 | assignments | 0 | 0 | 0 |
| _projects/lessons/flask | 18 | 179 | lessons | 0 | 4 | 50 (deny:[masked] 1, deny:ocs-domain 23) |
| _projects/systems/messages | 18 | 115 | social | 1 | 3 | 0 |
| _projects/systems/profile | 18 | 67 | ai-chatbot | 0 | 6 | 4 (deny:[masked] 2) |
| assets/js | 18 | 844 | game-engine | 2 | 0 | 0 |
| _projects/lessons/csa1 | 17 | 123 | lessons | 1 | 2 | 1 (deny:[masked] 1) |
| hacks/breakout -> features/hacks/breakout | 17 | 219 | game-engine | 0 | 1 | 0 |
| images/murderMystery | 17 | 6830 | game-engine | 0 | 0 | 0 |
| images/presence-system | 17 | 447 | analytics | 0 | 0 | 0 |
| images/toolkit-nav-buttons | 17 | 2273 | assignments | 0 | 0 | 0 |
| _projects/lessons/python | 16 | 396 | lessons | 2 | 1 | 26 (deny:[masked] 1, deny:ocs-domain 2) |
| _includes/tailwind | 15 | 269 | assignments | 1 | 9 | 0 |
| _projects/systems/topics | 15 | 69 | lessons | 0 | 8 | 0 |
| _sass/open-coding | 15 | 62 | lessons | 0 | 0 | 0 |
| navigation (top-level files) | 15 | 180 | lessons | 136 | 8 | 73 (deny:[masked] 70, deny:ocs-domain 3) |
| _posts/student_toolkit | 14 | 108 | assignments | 8 | 0 | 0 |
| _projects/lessons/javascript | 14 | 274 | lessons | 0 | 4 | 1 (deny:[masked] 1) |
| assets/images | 14 | 329 | game-engine | 0 | 0 | 0 |
| hacks/plagiarism -> features/hacks/plagiarism | 14 | 218 | assignments | 9 | 0 | 12 (deny:[masked] 12) |
| _posts/CSSE | 13 | 86 | game-engine | 5 | 9 | 2 (deny:[masked] 1, deny:ocs-domain 1) |
| _projects/games/heist-exe | 13 | 3151 | game-engine | 0 | 1 | 0 |
| _projects/systems/calendar | 13 | 486 | calendar | 1 | 5 | 1 (deny:ocs-domain 1) |
| assets/js/javaUI | 13 | 59 | lessons | 18 | 10 | 0 |
| navigation/bitshift | 12 | 82154 | lessons | 0 | 6 | 9 (deny:[masked] 1, size:large 1) |
| _layouts | 11 | 397 | analytics | 8 | 1 | 3 (deny:[masked] 3) |
| images/course-brag | 11 | 3303 | lessons | 0 | 0 | 0 |
| images/jpa-lesson-images | 11 | 718 | lessons | 0 | 0 | 0 |
| images/p1group1images | 11 | 2574 | ? | 0 | 0 | 0 |
| _projects/games/pirate-hunt | 10 | 4511 | game-engine | 0 | 19 | 0 |
| hacks/cookie-clicker -> features/hacks/cookie-clicker | 10 | 84 | examples | 0 | 0 | 0 |
| _projects/games/platformer | 9 | 1296 | game-engine | 0 | 0 | 0 |
| _projects/games/spline-barriers | 9 | 2023 | game-engine | 0 | 1 | 0 |
| _projects/lessons/ds2 | 9 | 88 | lessons | 0 | 1 | 1 (deny:[masked] 1) |
| assets/css | 9 | 85 | lessons | 0 | 0 | 0 |
| assets/js/fortuneFinders | 9 | 178 | game-engine | 0 | 6 | 2 (deny:ocs-domain 2) |
| images/backend | 9 | 374 | lessons | 0 | 0 | 0 |
| images/block-unlock | 9 | 66 | ? | 0 | 0 | 0 |
| images/snake | 9 | 1376 | game-engine | 0 | 0 | 0 |
| _projects/lessons/robot-logic-game | 8 | 69 | game-engine | 0 | 0 | 0 |
| _projects/lessons/sass | 8 | 156 | lessons | 1 | 7 | 29 (deny:[masked] 29) |
| assets/audio | 8 | 289 | ? | 0 | 0 | 0 |
| assets/js/GameEnginev1.1 | 8 | 58 | game-engine | 0 | 4 | 0 |
| assets/js/murderMystery | 8 | 35 | ? | 19 | 2 | 1 (deny:[masked] 1) |
| assets/screenQueue | 8 | 248 | hallpass | 0 | 0 | 4 (deny:[masked] 1, deny:ocs-domain 3) |
| assets/sounds/mansionGame | 8 | 36779 | game-engine | 0 | 0 | 2 (deny:[masked] 2) |
| hacks/TheBig6 -> features/hacks/TheBig6 | 8 | 194 | examples | 40 | 2 | 0 |
| images/gamify | 8 | 1247 | game-engine | 0 | 0 | 0 |
| images/west-coast | 8 | 2387 | examples | 0 | 0 | 0 |
| navigation/authentication | 8 | 43 | analytics | 9 | 0 | 1 (deny:[masked] 1) |
| _notebooks/student_toolkit | 7 | 44 | assignments | 6 | 0 | 9 (deny:[masked] 7) |
| _posts/forum | 7 | 55 | social | 0 | 0 | 0 |
| _projects/lessons/_includes | 7 | 10 | lessons | 2 | 6 | 0 |
| _sass/capstone | 7 | 40 | lessons | 0 | 0 | 2 (deny:[masked] 2) |
| assets/js/api | 7 | 22 | social | 0 | 1 | 1 (secret:password-literal 1) |
| assets/js/logicGateGame | 7 | 1 | game-engine | 0 | 0 | 0 |
| hacks/ai-make-debugger -> features/hacks/ai-make-debugger | 7 | 5 | ai-chatbot | 0 | 0 | 0 |
| _notebooks/gamify | 6 | 64 | game-engine | 0 | 0 | 0 |
| _projects/games/tag-game-multiplayer | 6 | 4593 | game-engine | 0 | 6 | 4 (deny:ocs-domain 4) |
| assets/js/mini-lesson | 6 | 41 | lessons | 1 | 0 | 0 |
| assets/js/solitaire | 6 | 23 | game-engine | 0 | 0 | 0 |
| assets/sounds/crypto | 6 | 4736 | finance-sim | 0 | 0 | 2 (deny:[masked] 2) |
| images/crud | 6 | 1215 | ? | 0 | 0 | 0 |
| images/plagiarism | 6 | 3 | assignments | 0 | 0 | 0 |
| _posts (top-level files) | 5 | 40 | social | 1 | 3 | 0 |
| _projects | 5 | 117 | game-engine | 0 | 8 | 7 (deny:[masked] 3) |
| _projects/systems/scraper | 5 | 17 | lessons | 0 | 0 | 0 |
| _themes/so-simple | 5 | 2 | social | 0 | 7 | 0 |
| _themes/text | 5 | 11 | social | 0 | 26 | 0 |
| hacks/connect4 -> features/hacks/connect4 | 5 | 35 | game-engine | 1 | 0 | 0 |
| images/MansionGameBlog | 5 | 961 | game-engine | 0 | 0 | 0 |
| images/agile | 5 | 3231 | ? | 0 | 0 | 0 |
| images/basic-api | 5 | 28 | ? | 0 | 0 | 0 |
| images/connect4 | 5 | 1165 | game-engine | 0 | 0 | 0 |
| images/help | 5 | 17195 | hallpass | 0 | 0 | 0 |
| images/jarvis | 5 | 7376 | ai-chatbot | 0 | 0 | 0 |
| images/sip | 5 | 1946 | ? | 0 | 0 | 0 |
| _includes/csp | 4 | 62 | lessons | 0 | 0 | 0 |
| _posts/Maps_Persona | 4 | 8 | social | 1 | 0 | 4 (deny:[masked] 4) |
| _projects/games/nod-game | 4 | 60 | game-engine | 0 | 0 | 0 |
| _projects/lessons/game-lessons | 4 | 30 | lessons | 0 | 2 | 0 |
| _projects/systems/test-project | 4 | 5 | lessons | 0 | 1 | 0 |
| _themes/cayman | 4 | 3 | social | 0 | 2 | 0 |
| _themes/hydejack | 4 | 3 | social | 0 | 2 | 0 |
| _themes/minima | 4 | 3 | social | 0 | 1 | 0 |
| _themes/yat | 4 | 3 | social | 0 | 8 | 0 |
| assets/Bathroom | 4 | 14 | hallpass | 0 | 0 | 0 |
| assets/js/GameEnginev1 | 4 | 13 | game-engine | 0 | 6 | 0 |
| assets/js/crypto | 4 | 73 | finance-sim | 0 | 0 | 0 |
| assets/js/nitd_rpg | 4 | 23 | game-engine | 0 | 0 | 0 |
| hacks/navigation -> features/hacks/navigation | 4 | 0 | examples | 0 | 0 | 0 |
| images/activity | 4 | 4441 | game-engine | 0 | 0 | 0 |
| images/classroomcsa | 4 | 14076 | lessons | 0 | 0 | 0 |
| images/cpt | 4 | 571 | ? | 0 | 0 | 0 |
| images/gamebuilder | 4 | 261 | game-engine | 0 | 0 | 0 |
| images/gamify-tools | 4 | 332 | game-engine | 0 | 0 | 0 |
| images/jokes | 4 | 1783 | social | 0 | 0 | 0 |
| images/personas | 4 | 1718 | ? | 0 | 0 | 0 |
| images/sprints | 4 | 4987 | calendar | 0 | 0 | 0 |
| navigation/toolkit | 4 | 79 | assignments | 0 | 0 | 0 |
| _posts/GameLessons | 3 | 47 | lessons | 0 | 0 | 0 |
| _projects/lessons/graph-heuristics | 3 | 73 | lessons | 0 | 3 | 0 |
| _projects/systems/auto-generator | 3 | 34 | ? | 0 | 0 | 0 |
| assets | 3 | 6728 | assignments | 0 | 0 | 0 |
| assets/audio/projects | 3 | 27785 | game-engine | 0 | 0 | 1 (size:large 1) |
| assets/icons | 3 | 20 | social | 0 | 0 | 0 |
| assets/js/block-unlock | 3 | 29 | game-engine | 8 | 1 | 0 |
| assets/js/digitalFamine | 3 | 100 | game-engine | 9 | 4 | 0 |
| images/aws | 3 | 574 | ? | 0 | 0 | 0 |
| images/computing-bias | 3 | 163 | ? | 0 | 0 | 0 |
| images/cookie | 3 | 118 | ? | 0 | 0 | 0 |
| images/csa-chat | 3 | 519 | social | 0 | 0 | 0 |
| images/csa-frqs | 3 | 242 | lessons | 0 | 0 | 0 |
| images/csp-period-5-flowcharts | 3 | 47 | lessons | 0 | 0 | 0 |
| images/data_structures | 3 | 179 | lessons | 0 | 0 | 0 |
| images/documentation | 3 | 35 | ? | 0 | 0 | 0 |
| images/fortuneFinders | 3 | 5 | game-engine | 0 | 0 | 0 |
| images/fullstack | 3 | 36 | ? | 0 | 0 | 0 |
| images/solitaire | 3 | 115 | game-engine | 0 | 0 | 0 |
| images/tictactoe | 3 | 131 | lessons | 0 | 0 | 0 |
| navigation/Game-OOP | 3 | 17 | game-engine | 0 | 2 | 0 |
| scripts/omake | 3 | 4 | lessons | 0 | 0 | 0 |
| (root files) | 2 | 5 | ? | 0 | 1 | 0 |
| _includes/runners | 2 | 5 | ? | 0 | 0 | 0 |
| _includes/tools | 2 | 30 | game-engine | 0 | 0 | 0 |
| _notebooks/Math | 2 | 98 | lessons | 0 | 0 | 1 (deny:[masked] 1) |
| _posts/Plot | 2 | 3 | social | 0 | 0 | 0 |
| _posts/documentation | 2 | 28 | social | 1 | 1 | 0 |
| _projects/systems/ai-ab-engine | 2 | 11 | ai-chatbot | 0 | 3 | 0 |
| _projects/systems/auto-generator-ui | 2 | 22 | ? | 0 | 0 | 0 |
| _sass/[masked] | 2 | 26 | lessons | 0 | 0 | 0 |
| assets/audio/5 Sec Crowd Cheer - Sound Effect (HD).mp3.download | 2 | 1 | ? | 0 | 0 | 0 |
| assets/css/aesthetihawk | 2 | 1 | ? | 0 | 0 | 0 |
| assets/js/pages | 2 | 4 | game-engine | 0 | 0 | 0 |
| assets/js/vendor | 2 | 225 | ? | 0 | 1 | 0 |
| hacks/api-usage -> features/hacks/api-usage | 2 | 18 | examples | 1 | 0 | 1 (deny:[masked] 1) |
| images/Penguins-jslessons | 2 | 60 | lessons | 0 | 0 | 0 |
| images/bathroom | 2 | 638 | hallpass | 0 | 0 | 0 |
| images/breakoutHacks | 2 | 54 | game-engine | 0 | 0 | 0 |
| images/tools | 2 | 310 | ? | 0 | 0 | 0 |
| images/u8-p1-assets | 2 | 201 | ? | 0 | 0 | 0 |
| navigation/bootstrap | 2 | 8 | ? | 3 | 0 | 2 (deny:[masked] 2) |
| navigation/sabresprings | 2 | 20 | ? | 4 | 0 | 0 |
| navigation/tailwind | 2 | 9 | ? | 3 | 0 | 1 (deny:[masked] 1) |
| tests/fixtures | 2 | 4 | lessons | 0 | 0 | 0 |
| _includes/ScreenQueue | 1 | 1 | hallpass | 5 | 0 | 0 |
| _includes/aesthetihawk | 1 | 3 | ? | 0 | 0 | 0 |
| _includes/submissions | 1 | 3 | assignments | 0 | 0 | 0 |
| _notebooks (top-level files) | 1 | 8 | lessons | 0 | 0 | 0 |
| _notebooks/English | 1 | 29 | lessons | 0 | 0 | 1 (deny:[masked] 1) |
| _projects/games/gamify | 1 | 4647 | game-engine | 0 | 0 | 0 |
| _projects/lessons/ap-frq-simulator | 1 | 49 | lessons | 1 | 0 | 0 |
| _projects/lessons/cpt-concepts | 1 | 23 | lessons | 0 | 0 | 0 |
| _projects/lessons/make-helper | 1 | 5 | lessons | 0 | 0 | 0 |
| _projects/lessons/parallelAndComputinglesson | 1 | 9 | lessons | 0 | 0 | 0 |
| assets/data | 1 | 0 | lessons | 0 | 0 | 0 |
| assets/js/capstone | 1 | 11 | assignments | 0 | 0 | 0 |
| assets/js/chat | 1 | 29 | social | 0 | 0 | 0 |
| assets/js/spaceColony | 1 | 57 | game-engine | 0 | 0 | 0 |
| assets/js/submissions | 1 | 16 | assignments | 0 | 0 | 0 |
| hacks/mathhelper -> features/hacks/mathhelper | 1 | 7 | hallpass | 0 | 0 | 0 |
| images/docs | 1 | 144 | ? | 0 | 0 | 0 |
| images/javaml | 1 | 9588 | lessons | 0 | 0 | 0 |
| images/rps | 1 | 889 | ? | 0 | 0 | 0 |
| navigation/adventureGame | 1 | 13 | game-engine | 0 | 0 | 0 |
| navigation/documentation | 1 | 39 | ? | 0 | 0 | 0 |
| navigation/predictors | 1 | 5 | assignments | 0 | 0 | 0 |
| pages/assets | 1 | 4 | game-engine | 0 | 5 | 0 |
| scripts/test_support | 1 | 3 | ? | 0 | 0 | 0 |

## Changed upstream paths, by kit feature

- **(unclaimed: core)** (127): 404.html, Gemfile, README4YML.md, _data/aesthetihawk_sidebar.yml, _includes/admin_foundation.html, _includes/aesthetihawk/sidebar.html, _includes/challenge-submit-button.html, _includes/code-runner.html ...
- **ai-chatbot** (5): _layouts/base_chatadpt.html, _layouts/base_chatog.html, _layouts/certchatbot.html, _layouts/chatbot.html, assets/js/chatbot.js
- **analytics** (5): _layouts/analytics-dashboard.html, assets/js/ocs-analytics-dashboard.js, assets/js/ocs-analytics-enhanced.js, assets/js/ocs-analytics-tracker.js, navigation/toolkit/analytics_help.md
- **assignments** (4): navigation/predictors/exam-predictor.html, navigation/predictors/grade-predictor.html, navigation/predictors/study-tracker.html, navigation/toolkit/student.md
- **code-runner** (5): assets/js/pages/runners/core/BaseRunner.js, assets/js/pages/runners/core/ButtonFeedback.js, assets/js/pages/runners/core/EditorManager.js, assets/js/pages/runners/executors/GameExecutor.js, assets/js/pages/runners/index.js
- **examples** (33): hacks/cookie-clicker/assets/baseCookie.png, hacks/snake.md, media/assets/abcL.png, media/assets/atlanticL.png, media/assets/bbcC.png, media/assets/buzzfeedL.png, media/assets/callerR.png, media/assets/cbnR.png ...
- **game-engine** (71): _projects/games/castle-game/images/FortressLevelEndScreen.png, _projects/games/castle-game/images/castleGameEndScreen.png, _projects/games/castle-game/images/dungeon.jpg, _projects/games/castle-game/images/mortyKnight.png, _projects/games/castle-game/images/target.png, _projects/games/gamify/docs/OVERVIEW.md, _projects/games/gamify/favicon.png, _projects/games/gamify/images/chickenj.png ...
- **kasm** (7): images/kasm/kasmUI.png, images/kasm/kasmlogin.png, images/kasm/kasmsignup.png, images/kasm/kasmv2.png, images/kasm/launch.png, images/kasm/ncslogin.png, images/kasm/vskasm.png
- **lessons** (18): _data/os_infograph.yml, _data/tools_infograph.yml, _data/tools_questions.yml, _includes/nav/home.html, _includes/nav/homejava.html, _includes/nav/javascript_project.html, _includes/tailwind/infograph.html, images/javaml/weka.jpeg ...
- **social** (1): _includes/microblog_foundation.html

Changed paths that would fail the scrub gate (13):

- _includes/slim_sidebar.html: _includes/slim_sidebar.html:482: [secret:api-key-literal] API_KE...(40 chars)  -- hard-coded API key/token
- _layouts/base_chatadpt.html: _layouts/base_chatadpt.html:406: [secret:google-api-key] AIzaSy...(39 chars)  -- looks like a credential; remove it and revoke it at the source
- _layouts/base_chatog.html: _layouts/base_chatog.html:406: [secret:google-api-key] AIzaSy...(39 chars)  -- looks like a credential; remove it and revoke it at the source
- _layouts/certchatbot.html: _layouts/certchatbot.html:318: [secret:google-api-key] AIzaSy...(39 chars)  -- looks like a credential; remove it and revoke it at the source
- _layouts/chatbot.html: _layouts/chatbot.html:321: [secret:google-api-key] AIzaSy...(39 chars)  -- looks like a credential; remove it and revoke it at the source
- _projects/games/gamify/index.ipynb: _projects/games/gamify/index.ipynb:23: [deny:[masked]] [masked]  -- school/teacher-specific term
- _sass/minima/custom-styles.scss: _sass/minima/custom-styles.scss:30: [deny:[masked]] [masked]  -- school/teacher-specific term
- assets/js/GameEnginev1/GameLevelAirport.js: assets/js/GameEnginev1/GameLevelAirport.js:16: [deny:ocs-domain] [masked]  -- school/teacher-specific term
- assets/js/chatbot.js: assets/js/chatbot.js:19: [deny:[masked]] [masked]  -- school/teacher-specific term
- media/media.md: features/media-literacy/media.md:15: [deny:[masked]] [masked]  -- school/teacher-specific term
- navigation/authentication/login.md: navigation/authentication/login.md:84: [deny:[masked]] [masked]  -- school/teacher-specific term
- navigation/toolkit/analytics_help.md: navigation/toolkit/analytics_help.md:10: [deny:[masked]] [masked]  -- school/teacher-specific term
- navigation/toolkit/student.md: navigation/toolkit/student.md:27: [deny:[masked]] [masked]  -- school/teacher-specific term

## Protected kit files that differ upstream (never overwritten)

- .gitignore (changed)
- Makefile (changed)
- README.md (changed)
- _config.yml (changed)
- _includes/head-custom.html (changed)
- _posts/capstone/Jarvis3000/README.md (new)
- _posts/capstone/README.md (new)
- _projects/.makeprojects (changed)
- _projects/README.md (new)
- _projects/_template/Makefile (changed)
- _projects/games/mansionGame/docs/README.md (new)
- _projects/games/pirate-hunt/docs/README.md (new)
- _projects/games/racing-game/docs/README.md (new)
- _projects/games/voidstriker/docs/README.md (new)
- _projects/lessons/ap-frq-simulator/README.md (new)
- _projects/lessons/cpt-concepts/README.md (new)
- _projects/lessons/flask/README.md (new)
- _projects/lessons/graph-heuristics/README.md (new)
- _projects/lessons/java/docs/README.md (new)
- _projects/lessons/javascript/docs/README.md (new)
- _projects/lessons/make-helper/README.md (new)
- _projects/lessons/python/docs/README.md (new)
- _projects/lessons/sass/docs/README.md (new)
- _projects/systems/blogs/README.md (new)
- _projects/systems/calendar/README.md (new)
- _projects/systems/courses/README.md (new)
- _projects/systems/gamebuilder/README.md (new)
- _projects/systems/messages/README.md (new)
- _projects/systems/profile/README.md (new)
- _projects/systems/topics/README.md (new)
- _sass/open-coding/README.md (changed)
- _themes/cayman/_config.yml (new)
- _themes/hydejack/_config.yml (new)
- _themes/minima/_config.yml (new)
- _themes/so-simple/_config.yml (new)
- _themes/text/_config.yml (new)
- _themes/yat/_config.yml (new)
- assets/js/GameEnginev1.1/test/README.md (new)
- assets/js/api/config.js (changed)
- assets/js/vendor/README.md (new)


_434 school-specific term(s) masked as [masked]; run scan --no-mask locally to see them._
