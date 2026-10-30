# Nuke runs this because the JKrender folder is on its plugin path:
#     nuke.pluginAddPath("./JKrender")   in ~/.nuke/init.py
import JKrender_NukeIntegration as _jk

_jk.install_menu()
