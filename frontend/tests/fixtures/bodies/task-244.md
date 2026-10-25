Fix regin defect #191, reported against feature #112 (attribute all current defects):

The sweep's rule 'every verb passes --session' cannot hold for feature creation: 'regin feature new' has no --session option (nor --project), so the five R6 features #113-#117 carry no link to the session that minted them and no project.
reported in session 005e7856-fd68-418b-b2ac-2b63bb2926c0
suspect paths: cli/commands/feature.py