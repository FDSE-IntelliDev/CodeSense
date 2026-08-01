import java

from CompilationUnit unit
where unit.fromSource() and unit.isSourceFile()
select unit.getRelativePath()
