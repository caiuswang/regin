Fix regin defect #220, reported against feature #126 (Fix session trace detail defects):

目前在terminal会话的场景下会出现一些agent的消息被当成用户消息，然后展示在用户turn里面在session detail view page， 找到一个可靠的方法修复它
span id: promptlive-e497b3e404e5f