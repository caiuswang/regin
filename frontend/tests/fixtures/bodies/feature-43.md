Work on Linear issue CAI-213:

<issue identifier="CAI-213">
<title>Fix armed task running</title>
<description>
Now this task's dependened task has already done, but why this task is still armed. 

The expected behavior is that if one task is awaiting its dependent tasks to be finished, then if all of its dependent tasks have been done, it should be run automatically. But why is it not?

task id: 23
</description>
<team name="Caiuswang"/>
<label>Bug</label>
<project name="regin">agent harness tool</project>
</issue>