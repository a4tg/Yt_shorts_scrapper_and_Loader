if (['#verify=', '#reset='].some((prefix) => location.hash.startsWith(prefix))) {
  location.replace(`/app${location.hash}`);
}
