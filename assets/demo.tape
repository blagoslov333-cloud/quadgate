# Charmbracelet VHS script for QuadGate 60-second terminal demo
# Render GIF with: vhs assets/demo.tape

Output assets/demo.gif

Require quadgate

Set Shell "bash"
Set FontFamily "JetBrains Mono"
Set FontSize 20
Set Width 1200
Set Height 620
Set Padding 24
Set Theme "GitHub Dark"
Set TypingSpeed 40ms
Set Framerate 30

Sleep 500ms

Type "quadgate init --harness antigravity"
Enter
Sleep 1.2s

Type "quadgate doctor"
Enter
Sleep 1.8s

Type "quadgate bench --dry-replay"
Enter
Sleep 3s
