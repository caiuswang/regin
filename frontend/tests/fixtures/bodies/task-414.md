Fix regin defect #268, reported against feature #125 (Pipeline所有页面以及操作逻辑评估以及重新设计-二期):

目前有些Task第一次Run的时候总会提示：“launch refused: claude not found on the server's PATH” ，但是后面再点一次就正常运行了， 比如task 405