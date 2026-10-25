Fix regin defect #216, reported against feature #101 (Better defect fix operation):

如果defects里面包含图片，就会导致没有占位符，导致到task也只是attach images, 然后到真正prompt的时候agent可能都不知道有图片