Fix regin defect #222, reported against feature #127 (regin项目宣传视频):

serve --replica leaves the terminal WebSocket (GET /api/sessions/<id>/terminal) open: a logged-in replica viewer on the same host can type into live tmux panes, contrary to I6/I7's no-real-world-effects intent; spec says only non-GET
reported in session f356d38d-0730-4883-b861-8c16950dcd8a