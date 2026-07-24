import java

from Import imported
where imported.fromSource()
select
  imported.getCompilationUnit().getRelativePath(),
  imported.toString(),
  "import"
