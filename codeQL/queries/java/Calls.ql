import CodeSearchModel

from Call call, Callable caller, Callable callee
where
  caller = call.getCaller().getSourceDeclaration() and
  callee = call.getCallee() and
  projectCallable(caller)
select
  callableKey(caller),
  callableKey(callee),
  caller.getName(),
  callee.getName(),
  caller.getFile().getRelativePath(),
  call.getLocation().getStartLine(),
  call.getLocation().getStartColumn(),
  call.toString()
