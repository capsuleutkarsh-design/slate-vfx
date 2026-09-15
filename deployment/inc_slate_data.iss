// ---------------------------------------------------------------------------
// Shared data handling for the three Slate installers.
//
// Uninstalling used to leave every setting and the whole database behind, in
// folders nobody was told about. That is the right default - somebody removing
// a build to install a newer one wants their studio back afterwards - but it
// has no opposite, and without one a bad install cannot be undone. Reinstalling
// over the top picks the stale files straight back up and lands in exactly the
// state that made somebody reinstall.
//
// So this asks, once, at uninstall time, and does nothing at all unless the
// answer is yes.
//
// This file is included from inside each script's [Code] section, so it has no
// section headers of its own.
//
//   /PURGEDATA   delete the settings and data without asking
//   /KEEPDATA    keep them without asking (this is also what silent does)
// ---------------------------------------------------------------------------

var
  PurgeDataChoice: Boolean;
  PurgeDataAsked: Boolean;
  PurgeFailures: String;

procedure TryDeleteFileIfExists(const FilePath: String);
begin
  if FileExists(FilePath) then
  begin
    if DeleteFile(FilePath) then
      Log('Deleted file: ' + FilePath)
    else
    begin
      Log('Could not delete file: ' + FilePath);
      PurgeFailures := PurgeFailures + FilePath + #13#10;
    end;
  end;
end;

function SafeConstant(const Name: String): String;
begin
  // ExpandConstant raises on a name Setup does not know, and an exception in an
  // uninstall step aborts the whole step - the guard, the delete, and
  // everything after them. One misspelled constant ({userprofile}, which Inno
  // does not define) did exactly that: Setup showed "Internal error: Unknown
  // constant", nothing was deleted, and the uninstall still reported success.
  try
    Result := RemoveBackslashUnlessRoot(ExpandConstant(Name));
  except
    Result := '';
    Log('Setup does not know the constant ' + Name + ' - guard skipped it.');
  end;
end;

function SameFolder(const Candidate: String; const ConstantName: String): Boolean;
var
  Other: String;
begin
  Other := SafeConstant(ConstantName);
  Result := (Other <> '') and (CompareText(Candidate, Other) = 0);
end;

function LooksDeletable(const DirPath: String): Boolean;
var
  Candidate: String;
begin
  // DelTree is given whole directory trees here, so the guard belongs in front
  // of it rather than in the release notes. A drive root, a bare UNC share or
  // one of the profile folders themselves is never what was meant, and by the
  // time anybody notices it is already gone.
  Result := False;
  Candidate := RemoveBackslashUnlessRoot(Trim(DirPath));

  if Length(Candidate) < 8 then exit;
  if Length(ExtractFileName(Candidate)) = 0 then exit;

  if SameFolder(Candidate, '{localappdata}') then exit;
  if SameFolder(Candidate, '{userappdata}') then exit;
  if SameFolder(Candidate, '{%USERPROFILE}') then exit;
  if SameFolder(Candidate, '{userdocs}') then exit;
  if SameFolder(Candidate, '{win}') then exit;
  if SameFolder(Candidate, '{sys}') then exit;
  if SameFolder(Candidate, '{pf}') then exit;
  if SameFolder(Candidate, '{pf32}') then exit;

  Result := True;
end;

procedure TryDeleteDirIfExists(const DirPath: String);
begin
  if not DirExists(DirPath) then
    exit;

  if not LooksDeletable(DirPath) then
  begin
    Log('Refused to delete, this does not look like a data folder: ' + DirPath);
    exit;
  end;

  if DelTree(DirPath, True, True, True) then
    Log('Deleted directory: ' + DirPath)
  else
  begin
    Log('Could not fully delete directory: ' + DirPath);
    PurgeFailures := PurgeFailures + DirPath + #13#10;
  end;
end;

function StudioFolderAccepted(const Folder: String): Boolean;
begin
  // The studio's shared folder. This page used to be pre-filled with one
  // studio's drive letter, so pressing Next without reading it installed a
  // machine pointed at a drive that does not exist here - and nothing said so
  // until somebody wondered why nothing was shared.
  Result := False;

  if Trim(Folder) = '' then
  begin
    MsgBox('Slate needs to know where this studio keeps its shared folder.'
           + #13#10#13#10
           + 'There is no sensible default - every studio uses a different '
           + 'drive - so this one cannot be left blank.', mbError, MB_OK);
    exit;
  end;

  if not DirExists(Folder) then
  begin
    if MsgBox('This folder does not exist on this machine:' + #13#10#13#10
              + Folder + #13#10#13#10
              + 'That is fine if the drive has not been mapped yet, or if the '
              + 'folder is about to be made. It is not fine if it is a typo - '
              + 'Slate would work locally and share nothing.' + #13#10#13#10
              + 'Use it anyway?',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then
      exit;
  end;

  Result := True;
end;

function IsUninstallerFile(const Name: String): Boolean;
begin
  // unins000.exe is the program doing the uninstalling, and unins000.dat is the
  // list it is working from. Windows will not delete a running program, and
  // Setup removes its own files after this step anyway - so handing them to
  // DelTree only produces a failure report about files that were never a
  // problem, which is exactly what it did.
  Result := CompareText(Copy(Name, 1, 5), 'unins') = 0;
end;

procedure PurgeDirectoryKeeping(const DirPath: String; const KeepName: String);
var
  FindRec: TFindRec;
  Child: String;
begin
  // Empty a folder rather than delete it. Used for the program folder, which
  // still holds the running uninstaller at this point, and for the shared
  // settings folder, where one named file may have to survive because
  // another Slate product still reads it.
  if not DirExists(DirPath) then
    exit;

  if not LooksDeletable(DirPath) then
  begin
    Log('Refused to clear, this does not look like a data folder: ' + DirPath);
    exit;
  end;

  if FindFirst(AddBackslash(DirPath) + '*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..')
           and (not IsUninstallerFile(FindRec.Name))
           and ((KeepName = '') or (CompareText(FindRec.Name, KeepName) <> 0)) then
        begin
          Child := AddBackslash(DirPath) + FindRec.Name;
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            TryDeleteDirIfExists(Child)
          else
            TryDeleteFileIfExists(Child);
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;

  // Whatever is left belongs to Setup, and Setup removes it. A failure here is
  // not worth reporting, so RemoveDir is called directly.
  if KeepName = '' then
    RemoveDir(DirPath)
  else
    Log('Kept ' + KeepName + ' in ' + DirPath);
end;

procedure PurgeDirectory(const DirPath: String);
begin
  PurgeDirectoryKeeping(DirPath, '');
end;

procedure RemoveOldProgramFiles(const OldDir: String);
begin
  // Slate used to be installed straight into {localappdata}\Slate, which is
  // also where it keeps config.json, the offline database, the cache and the
  // logs. The program now lives under {localappdata}\Programs, and what an
  // older install left in the old folder is removed here by name - the
  // program files, never the data beside them.
  if not DirExists(OldDir) then
    exit;

  Log('Removing an older build''s program files from ' + OldDir);
  TryDeleteDirIfExists(AddBackslash(OldDir) + '_internal');
  TryDeleteDirIfExists(AddBackslash(OldDir) + 'slate');
  TryDeleteDirIfExists(AddBackslash(OldDir) + 'external');
  TryDeleteDirIfExists(AddBackslash(OldDir) + 'OpenRV');
  TryDeleteDirIfExists(AddBackslash(OldDir) + 'database');
  TryDeleteDirIfExists(AddBackslash(OldDir) + 'slate_server');

  TryDeleteFileIfExists(AddBackslash(OldDir) + 'Slate_Studio.exe');
  TryDeleteFileIfExists(AddBackslash(OldDir) + 'Slate_Ops.exe');
  TryDeleteFileIfExists(AddBackslash(OldDir) + 'Slate_Server.exe');
  TryDeleteFileIfExists(AddBackslash(OldDir) + 'Slate.exe');
  TryDeleteFileIfExists(AddBackslash(OldDir) + 'Slate_Debug.exe');
  TryDeleteFileIfExists(AddBackslash(OldDir) + 'SlateUpdater.exe');
  TryDeleteFileIfExists(AddBackslash(OldDir) + 'unins000.exe');
  TryDeleteFileIfExists(AddBackslash(OldDir) + 'unins000.dat');
  TryDeleteFileIfExists(AddBackslash(OldDir) + 'unins000.msg');
end;

function HasCmdLineSwitch(const Name: String): Boolean;
var
  I: Integer;
  Param: String;
  Wanted: String;
begin
  Result := False;
  Wanted := '/' + Uppercase(Name);
  for I := 1 to ParamCount do
  begin
    Param := Uppercase(Trim(ParamStr(I)));
    if (Param = Wanted) or (Param = Wanted + '=1') or (Param = Wanted + '=YES') then
    begin
      Result := True;
      exit;
    end;
  end;
end;

function ShouldRemoveData(const Product: String; const Detail: String): Boolean;
begin
  // Asked once per run. Two uninstall steps consult this and a second dialog
  // would look like the first one had not registered.
  if PurgeDataAsked then
  begin
    Result := PurgeDataChoice;
    exit;
  end;
  PurgeDataAsked := True;

  if HasCmdLineSwitch('PURGEDATA') then
  begin
    PurgeDataChoice := True;
    Log('Data removal requested on the command line.');
  end
  else if HasCmdLineSwitch('KEEPDATA') or UninstallSilent then
  begin
    // An unattended run cannot answer a question, and the answer it cannot
    // take back is the destructive one.
    PurgeDataChoice := False;
    Log('Unattended uninstall: keeping the data.');
  end
  else
    PurgeDataChoice :=
      MsgBox(Product + ' is being removed.' + #13#10#13#10 +
             'Delete its settings and data as well?' + #13#10#13#10 +
             Detail + #13#10#13#10 +
             'No  -  leave them where they are.' + #13#10 +
             'Yes -  delete them, so the next install starts from nothing.' + #13#10#13#10 +
             'This cannot be undone. Take a backup first if you are not sure.',
             mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;

  Result := PurgeDataChoice;
end;

procedure ReportPurgeFailures();
begin
  // Something still holding a file is the normal way this half-works, and a
  // half-deleted folder is picked straight back up by the next install - which
  // is the fault this whole option exists to fix. So it is said out loud.
  if PurgeFailures = '' then
    exit;

  if not UninstallSilent then
    MsgBox('These could not be deleted, most likely because something still has '
           + 'them open:' + #13#10#13#10 + PurgeFailures + #13#10
           + 'Restart Windows and delete them by hand before installing again, '
           + 'or the next install will pick them up.',
           mbError, MB_OK);
  PurgeFailures := '';
end;
