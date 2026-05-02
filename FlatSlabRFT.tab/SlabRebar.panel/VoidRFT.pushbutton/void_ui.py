# -*- coding: utf-8 -*-
"""VoidRFT dialog — dark WPF form.

Collects bar types and placement parameters from the user.
Returns a params dict on OK, or None on Cancel.

Usage (called from script.py after the floor is already picked):
    params = void_ui.show_dialog(doc, floor)
    if params is None:
        return  # cancelled
"""
from __future__ import print_function

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from pyrevit import forms

from Autodesk.Revit.DB import FilteredElementCollector, BuiltInParameter
from Autodesk.Revit.DB.Structure import RebarBarType

from System.Windows.Markup import XamlReader
from System.Windows.Controls import ComboBoxItem
from System.Windows.Input import MouseButton


MM_TO_FT = 1.0 / 304.8


def _type_name(element):
    """Return the display name of a Revit element type."""
    try:
        p = element.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p is not None:
            val = p.AsString()
            if val:
                return val
    except Exception:
        pass
    return str(element.Id.IntegerValue)


def _floor_display_name(floor):
    """Return a short display string for the selected floor."""
    try:
        p = floor.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if p is not None:
            mark = p.AsString()
            if mark:
                return 'Mark: {}'.format(mark)
    except Exception:
        pass
    try:
        name = floor.Name
        if name:
            return name
    except Exception:
        pass
    return 'Floor Id={}'.format(floor.Id.IntegerValue)


# ──────────────────────────────────────────────────────────────────────────────
#  XAML  —  dark WPF form, same visual language as FlatSlabRFT
# ──────────────────────────────────────────────────────────────────────────────
_XAML = u"""<Window
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="VoidRFT"
    Width="520" Height="640"
    WindowStartupLocation="CenterScreen"
    ResizeMode="NoResize"
    WindowStyle="None"
    Background="#09090F">

  <Window.Resources>

    <Style x:Key="DarkInput" TargetType="TextBox">
      <Setter Property="Background"      Value="#1E293B"/>
      <Setter Property="Foreground"      Value="#F1F5F9"/>
      <Setter Property="BorderBrush"     Value="#334155"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="Padding"         Value="10,8"/>
      <Setter Property="FontSize"        Value="13"/>
      <Setter Property="FontFamily"      Value="Segoe UI"/>
      <Setter Property="CaretBrush"      Value="#06B6D4"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="TextBox">
            <Border x:Name="bd"
                    Background="{TemplateBinding Background}"
                    BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}"
                    CornerRadius="7" Padding="{TemplateBinding Padding}">
              <ScrollViewer x:Name="PART_ContentHost"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsFocused" Value="True">
                <Setter TargetName="bd" Property="BorderBrush" Value="#06B6D4"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="FieldLabel" TargetType="TextBlock">
      <Setter Property="Foreground"  Value="#64748B"/>
      <Setter Property="FontSize"    Value="10"/>
      <Setter Property="FontWeight"  Value="SemiBold"/>
      <Setter Property="FontFamily"  Value="Segoe UI"/>
      <Setter Property="Margin"      Value="0,0,0,5"/>
    </Style>

    <Style x:Key="SectionHeader" TargetType="TextBlock">
      <Setter Property="Foreground"  Value="#06B6D4"/>
      <Setter Property="FontSize"    Value="10"/>
      <Setter Property="FontWeight"  Value="Bold"/>
      <Setter Property="FontFamily"  Value="Segoe UI"/>
      <Setter Property="Margin"      Value="0,0,0,12"/>
    </Style>

    <Style x:Key="DarkComboItem" TargetType="ComboBoxItem">
      <Setter Property="Foreground" Value="#111111"/>
      <Setter Property="Background" Value="White"/>
      <Setter Property="Padding"    Value="8,4"/>
      <Style.Triggers>
        <Trigger Property="IsHighlighted" Value="True">
          <Setter Property="Background" Value="#D7ECFF"/>
        </Trigger>
        <Trigger Property="IsSelected" Value="True">
          <Setter Property="Background" Value="#2B88D8"/>
          <Setter Property="Foreground" Value="White"/>
        </Trigger>
      </Style.Triggers>
    </Style>

    <Style x:Key="DarkCombo" TargetType="ComboBox">
      <Setter Property="Background"          Value="#1E293B"/>
      <Setter Property="Foreground"          Value="#111111"/>
      <Setter Property="BorderBrush"         Value="#334155"/>
      <Setter Property="BorderThickness"     Value="1"/>
      <Setter Property="Padding"             Value="10,8"/>
      <Setter Property="FontSize"            Value="13"/>
      <Setter Property="FontFamily"          Value="Segoe UI"/>
      <Setter Property="ItemContainerStyle"  Value="{StaticResource DarkComboItem}"/>
    </Style>

    <Style x:Key="RunBtn" TargetType="Button">
      <Setter Property="Foreground"      Value="White"/>
      <Setter Property="FontFamily"      Value="Segoe UI"/>
      <Setter Property="FontSize"        Value="14"/>
      <Setter Property="FontWeight"      Value="Bold"/>
      <Setter Property="Padding"         Value="40,13"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Root" CornerRadius="9" Padding="{TemplateBinding Padding}">
              <Border.Background>
                <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                  <GradientStop Color="#06B6D4" Offset="0"/>
                  <GradientStop Color="#8B5CF6" Offset="1"/>
                </LinearGradientBrush>
              </Border.Background>
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="Opacity" Value="0.88"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="CancelBtn" TargetType="Button">
      <Setter Property="Background"     Value="#1E293B"/>
      <Setter Property="Foreground"     Value="#94A3B8"/>
      <Setter Property="FontFamily"     Value="Segoe UI"/>
      <Setter Property="FontSize"       Value="13"/>
      <Setter Property="Padding"        Value="26,13"/>
      <Setter Property="Cursor"         Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Root"
                    Background="{TemplateBinding Background}"
                    BorderBrush="#334155" BorderThickness="1"
                    CornerRadius="9" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="BorderBrush" Value="#475569"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="CloseBtn" TargetType="Button">
      <Setter Property="Background"      Value="Transparent"/>
      <Setter Property="Foreground"      Value="#475569"/>
      <Setter Property="FontSize"        Value="14"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Width"           Value="30"/>
      <Setter Property="Height"          Value="30"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Root" Background="{TemplateBinding Background}" CornerRadius="5">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="Background" Value="#7F1D1D"/>
                <Setter Property="Foreground" Value="#FCA5A5"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="Card" TargetType="Border">
      <Setter Property="Background"      Value="#111827"/>
      <Setter Property="BorderBrush"     Value="#1E3A5F"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius"    Value="10"/>
      <Setter Property="Padding"         Value="18,14"/>
      <Setter Property="Margin"          Value="0,0,0,10"/>
    </Style>

    <Style x:Key="UnitCyan" TargetType="Border">
      <Setter Property="Background"      Value="#0E3A4A"/>
      <Setter Property="BorderBrush"     Value="#06B6D4"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius"    Value="0,7,7,0"/>
      <Setter Property="Padding"         Value="10,8"/>
      <Setter Property="Margin"          Value="-1,0,0,0"/>
    </Style>

  </Window.Resources>

  <Border BorderBrush="#1E3A5F" BorderThickness="1">
    <Grid>
      <Grid.RowDefinitions>
        <RowDefinition Height="40"/>
        <RowDefinition Height="*"/>
        <RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>

      <!-- TITLE BAR -->
      <Border Grid.Row="0" x:Name="TitleBar" Background="#0D1117">
        <Grid>
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="Auto"/>
          </Grid.ColumnDefinitions>
          <StackPanel Grid.Column="0" Orientation="Horizontal" VerticalAlignment="Center" Margin="14,0">
            <Ellipse Width="9" Height="9" Margin="0,0,7,0">
              <Ellipse.Fill>
                <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
                  <GradientStop Color="#06B6D4" Offset="0"/>
                  <GradientStop Color="#8B5CF6" Offset="1"/>
                </LinearGradientBrush>
              </Ellipse.Fill>
            </Ellipse>
            <TextBlock Text="VoidRFT" Foreground="#475569" FontSize="11"
                       FontFamily="Segoe UI" VerticalAlignment="Center"/>
          </StackPanel>
          <Border Grid.Column="1" x:Name="DragArea" Background="Transparent"/>
          <Button Grid.Column="2" x:Name="btnClose" Content="&#x2715;"
                  Style="{StaticResource CloseBtn}" Margin="0,0,8,0"/>
        </Grid>
      </Border>

      <!-- SCROLLABLE CONTENT -->
      <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto" Background="#09090F">
        <StackPanel Margin="20,16,20,8">

          <!-- Floor info -->
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="SELECTED FLOOR" Style="{StaticResource SectionHeader}"/>
              <TextBlock x:Name="tbFloorName"
                         Foreground="#94A3B8" FontSize="13" FontFamily="Segoe UI"
                         TextWrapping="Wrap"/>
            </StackPanel>
          </Border>

          <!-- Bar types -->
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="BAR TYPES" Style="{StaticResource SectionHeader}"/>

              <TextBlock Text="PHI 16 mm (2T16, 3T16, diagonal bars)"
                         Style="{StaticResource FieldLabel}"/>
              <ComboBox x:Name="cbBar16" Style="{StaticResource DarkCombo}" Margin="0,0,0,12"/>

              <TextBlock Text="PHI 18 mm (4T18, 5T18 edge bars)"
                         Style="{StaticResource FieldLabel}"/>
              <ComboBox x:Name="cbBar18" Style="{StaticResource DarkCombo}"/>
            </StackPanel>
          </Border>

          <!-- Parameters -->
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="PLACEMENT PARAMETERS" Style="{StaticResource SectionHeader}"/>

              <TextBlock Text="TRIMMER BAR SPACING" Style="{StaticResource FieldLabel}"/>
              <Grid Margin="0,0,0,12">
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="*"/>
                  <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <TextBox x:Name="tbSpacing" Text="100" Style="{StaticResource DarkInput}"/>
                <Border Grid.Column="1" Style="{StaticResource UnitCyan}">
                  <TextBlock Text="mm" Foreground="#06B6D4" FontSize="12"
                             FontWeight="SemiBold" FontFamily="Segoe UI" VerticalAlignment="Center"/>
                </Border>
              </Grid>

              <TextBlock Text="INNER OFFSET FROM VOID EDGE" Style="{StaticResource FieldLabel}"/>
              <Grid Margin="0,0,0,12">
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="*"/>
                  <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <TextBox x:Name="tbInner" Text="50" Style="{StaticResource DarkInput}"/>
                <Border Grid.Column="1" Style="{StaticResource UnitCyan}">
                  <TextBlock Text="mm" Foreground="#06B6D4" FontSize="12"
                             FontWeight="SemiBold" FontFamily="Segoe UI" VerticalAlignment="Center"/>
                </Border>
              </Grid>

              <TextBlock Text="LD MULTIPLIER (x Phi)" Style="{StaticResource FieldLabel}"/>
              <Grid Margin="0,0,0,12">
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="*"/>
                  <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <TextBox x:Name="tbLd" Text="57" Style="{StaticResource DarkInput}"/>
                <Border Grid.Column="1" Style="{StaticResource UnitCyan}">
                  <TextBlock Text="x D" Foreground="#06B6D4" FontSize="12"
                             FontWeight="SemiBold" FontFamily="Segoe UI" VerticalAlignment="Center"/>
                </Border>
              </Grid>

              <TextBlock Text="CONCRETE COVER" Style="{StaticResource FieldLabel}"/>
              <Grid Margin="0,0,0,12">
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="*"/>
                  <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <TextBox x:Name="tbCover" Text="40" Style="{StaticResource DarkInput}"/>
                <Border Grid.Column="1" Style="{StaticResource UnitCyan}">
                  <TextBlock Text="mm" Foreground="#06B6D4" FontSize="12"
                             FontWeight="SemiBold" FontFamily="Segoe UI" VerticalAlignment="Center"/>
                </Border>
              </Grid>

              <TextBlock Text="MIN TRIMMER LENGTH (skip threshold)"
                         Style="{StaticResource FieldLabel}"/>
              <Grid>
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="*"/>
                  <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <TextBox x:Name="tbMinLen" Text="300" Style="{StaticResource DarkInput}"/>
                <Border Grid.Column="1" Style="{StaticResource UnitCyan}">
                  <TextBlock Text="mm" Foreground="#06B6D4" FontSize="12"
                             FontWeight="SemiBold" FontFamily="Segoe UI" VerticalAlignment="Center"/>
                </Border>
              </Grid>

            </StackPanel>
          </Border>

        </StackPanel>
      </ScrollViewer>

      <!-- FOOTER -->
      <Border Grid.Row="2" Background="#0D1117" Padding="20,14,20,18">
        <StackPanel>
          <Border Height="1" Margin="0,0,0,14">
            <Border.Background>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="Transparent" Offset="0"/>
                <GradientStop Color="#334155"     Offset="0.5"/>
                <GradientStop Color="Transparent" Offset="1"/>
              </LinearGradientBrush>
            </Border.Background>
          </Border>
          <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btnCancel" Content="Cancel"
                    Style="{StaticResource CancelBtn}" Margin="0,0,12,0"/>
            <Button x:Name="btnRun" Content="&#x25B6;  Place Rebar"
                    Style="{StaticResource RunBtn}"/>
          </StackPanel>
        </StackPanel>
      </Border>

    </Grid>
  </Border>
</Window>"""


def _populate_bar_combo(cb, bar_types_all):
    """Fill a ComboBox with all available RebarBarType items, sorted by name."""
    cb.Items.Clear()
    for name in sorted(bar_types_all.keys()):
        item = ComboBoxItem()
        item.Content = name
        item.Tag = bar_types_all[name]
        cb.Items.Add(item)
    if cb.Items.Count > 0:
        cb.SelectedIndex = 0


def _read_combo_bar_type(cb):
    """Return the selected RebarBarType from a ComboBox, or None."""
    sel = cb.SelectedItem
    if sel is None:
        return None
    return sel.Tag


def _parse_mm(text, default, field_name):
    """Parse a mm value from a TextBox string. Returns (value, ok)."""
    try:
        v = float(str(text).strip())
        if v > 0:
            return v, True
    except (ValueError, TypeError):
        pass
    forms.alert('Invalid value for {}: "{}". Must be a positive number.'.format(
        field_name, text), title='VoidRFT Input Error')
    return default, False


def show_dialog(doc, floor):
    """Show the VoidRFT WPF dialog.

    Returns a params dict on OK, or None on Cancel.
    """
    bar_types_all_list = list(FilteredElementCollector(doc).OfClass(RebarBarType).ToElements())
    bar_types_all = {}
    for bt in bar_types_all_list:
        name = _type_name(bt)
        bar_types_all[name] = bt

    if not bar_types_all:
        forms.alert('No RebarBarType elements found in the project.\n'
                    'Load a rebar family before running VoidRFT.',
                    title='VoidRFT')
        return None

    floor_name = _floor_display_name(floor)

    # Read cover from floor as initial default for the cover input field.
    try:
        from void_slab_helpers import get_floor_cover
        cover_default_mm = int(round(get_floor_cover(floor) * 304.8))
    except Exception:
        cover_default_mm = 40

    window = XamlReader.Parse(_XAML)

    lbl_floor   = window.FindName('tbFloorName')
    cb_bar_16   = window.FindName('cbBar16')
    cb_bar_18   = window.FindName('cbBar18')
    tb_spacing  = window.FindName('tbSpacing')
    tb_inner    = window.FindName('tbInner')
    tb_ld       = window.FindName('tbLd')
    tb_cover    = window.FindName('tbCover')
    tb_min_len  = window.FindName('tbMinLen')
    btn_run     = window.FindName('btnRun')
    btn_cancel  = window.FindName('btnCancel')
    btn_close   = window.FindName('btnClose')
    title_bar   = window.FindName('TitleBar')

    lbl_floor.Text = floor_name
    tb_cover.Text = str(cover_default_mm)

    _populate_bar_combo(cb_bar_16, bar_types_all)
    _populate_bar_combo(cb_bar_18, bar_types_all)

    result = [None]

    def _on_run(sender, e):
        bt16 = _read_combo_bar_type(cb_bar_16)
        bt18 = _read_combo_bar_type(cb_bar_18)

        spacing_mm, ok  = _parse_mm(tb_spacing.Text,  100.0, 'Trimmer Spacing')
        if not ok: return
        inner_mm, ok    = _parse_mm(tb_inner.Text,     50.0, 'Inner Offset')
        if not ok: return
        ld_mult, ok     = _parse_mm(tb_ld.Text,         57.0, 'Ld Multiplier')
        if not ok: return
        cover_mm, ok    = _parse_mm(tb_cover.Text,      40.0, 'Cover')
        if not ok: return
        min_len_mm, ok  = _parse_mm(tb_min_len.Text,   300.0, 'Min Trimmer Length')
        if not ok: return

        result[0] = {
            'bar_type_16':           bt16,
            'bar_type_18':           bt18,
            'trimmer_spacing_mm':    spacing_mm,
            'inner_offset_mm':       inner_mm,
            'ld_multiplier':         ld_mult,
            'cover_mm':              cover_mm,
            'min_trimmer_length_mm': min_len_mm,
        }
        window.Close()

    def _on_cancel(sender, e):
        window.Close()

    def _on_title_bar_down(sender, e):
        if e.ChangedButton == MouseButton.Left:
            window.DragMove()

    btn_run.Click    += _on_run
    btn_cancel.Click += _on_cancel
    btn_close.Click  += _on_cancel
    title_bar.MouseDown += _on_title_bar_down

    window.ShowDialog()
    return result[0]
